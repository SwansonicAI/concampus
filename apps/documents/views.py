"""Dokumente: every document ConCampus makes (catalogue), each with a template of its own (core.paper.template), edited
as it prints: what stands around its content, and the content itself, written on the page (core/static/core/paper.js,
core/static/core/flow.js). Bescheide, messages and the texts of ConCampus's pages are worded on a page of their own,
beside them as they arrive. Two rights: whoever words texts writes the content, whoever designs the rest of the page."""
import json
import re

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.csp import CSP
from django.utils.html import conditional_escape, escape, linebreaks
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_POST
from lxml import html as lxml_html

from core import paper
from core.access import Refused, require
from core.models import Artwork, Institution, Layout, Text
from core.text import fold
from core.texts import ALWAYS, TEXTS, Values, basics, examples, render as worded

from . import compose
from .catalogue import KINDS, documents, shared
from .samples import letter, made_up, sample

CONTENT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.ttf': 'font/ttf'}
FRAMED = csp_override(settings.SECURE_CSP | {'frame-ancestors': [CSP.SELF]})  # a preview, shown in a frame of its page and nowhere else
WORDED = {'nachrichten', 'seiten'}  # kinds with nothing but wording: for whoever words texts only
SET_BY_PAPER = {'no-print', 'letterhead', 'address', 'dated', 'actions'}  # what the template draws itself, or nobody prints (core.paper.blocks)
PIXELS = re.compile(r'(\d+(?:\.\d+)?)px')
PLACEHOLDER = re.compile(r'\{(\w+)\}')


def index(request):
    return redirect('documents:bescheide')


def kinds(person):
    """The lists a person sees: whoever only designs, those of documents on paper; whoever does neither, none."""
    roles = person.roles
    return [kind for kind in KINDS if 'word_texts' in roles or 'design_papers' in roles and kind not in WORDED]


@require('word_texts', 'design_papers')
def listing(request, kind):
    """The documents of a kind, each with what it says and whether the Hochschule has made it its own. A search finds
    documents of every kind by their title or anything written on them."""
    if kind not in kinds(request.user):
        raise Refused(['word_texts'])
    q = request.GET.get('q', '').strip() if 'word_texts' in request.user.roles else ''
    own, sought, templates = {t.key: t for t in Text.objects.all()}, fold(q).split(), set(Layout.objects.values_list('key', flat=True))
    rows = []
    for d in documents().values():
        if not (says(d, own, sought) if q else d.kind == kind) or d.kind not in kinds(request.user):
            continue
        subject = (own[d.key] if d.key in own else TEXTS[d.key]).subject if d.kind != 'bescheinigungen' else ''
        rows.append((d, d.key in templates if d.kind == 'bescheinigungen' else d.key in own, pills(subject, d.key)))
    return render(request, 'documents/list.html', {
        'kind': kind, 'title': KINDS[kind][0], 'lead': KINDS[kind][1], 'q': q, 'rows': rows,
        'letter': kind == 'bescheide' and 'design_papers' in request.user.roles,
    })


def pills(text, key):
    """A text as the editor shows it (flow.js): each placeholder its example, on a tint, its name on pointing."""
    found = examples(key)
    return mark_safe(PLACEHOLDER.sub(lambda m: f'<span class="flow-pill" title="{m[1]}">{escape(found.get(m[1], m[1]))}</span>', escape(text)))


def says(document, own, sought):
    """Whether every word sought, folded, is in a document's title or anything written on it, as the Hochschule writes it."""
    said = [document.title]
    for key in document.texts:
        said += [own[key].subject, own[key].body] if key in own else [TEXTS[key].subject, TEXTS[key].body]
    if document.composed:
        content = compose.flow(document.key)
        said += [r.get('t', '') for b in content['blocks'] for found in compose.runs_of(b) for r in found]
    said = fold(' '.join(said))
    return all(word in said for word in sought)


class TextForm(forms.ModelForm):
    class Meta:
        model = Text
        fields = ['subject', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 8})}

    def clean(self):
        """Lines as ConCampus splits them: a browser sends \\r\\n, and „\\n\\n“ divides the paragraphs (core.texts.filled)."""
        return {name: value.replace('\r\n', '\n') for name, value in super().clean().items()}


def text_forms(document, data=None):
    """[(key, Spec, its form)] for each text worded on the document; a subject only where the text has one."""
    held = {t.key: t for t in Text.objects.filter(key__in=document.texts)}
    found = []
    for key in document.texts:
        spec = TEXTS[key]
        form = TextForm(data, prefix=key, instance=held.get(key) or Text(key=key, subject=spec.subject, body=spec.body))
        if not spec.subject:
            del form.fields['subject']
        found.append((key, spec, form))
    return found


def keep(key, spec, form):
    """A text saved where it changed; back on its standard, its record gone, where it says what the standard says."""
    if (form.cleaned_data.get('subject', spec.subject), form.cleaned_data['body']) == (spec.subject, spec.body):
        if form.instance.pk:
            form.instance.delete()
    elif form.has_changed() or not form.instance.pk:
        form.save()


def opened(request, kind, key):
    """The document asked for, and whether the person may word it; refused where there is nothing for them on it."""
    document = documents().get((kind, key))
    if document is None:
        raise Http404
    if kind not in kinds(request.user):
        raise Refused(['word_texts'])
    return document, 'word_texts' in request.user.roles


@require('word_texts', 'design_papers')
def document(request, kind, key):
    """One document. A Bescheinigung or Urkunde: its template, edited as it prints (editor). A Bescheid, a message or a
    page's text: worded beside it as it arrives, each text marked where other documents carry it too; saved together, a
    text only where it changed, back to its standard on asking."""
    document, word = opened(request, kind, key)
    if kind == 'bescheinigungen':
        return editor(request, key)
    back = reverse(f'documents:{kind}')
    texts = text_forms(document, request.POST or None) if word else []
    reset = next(((k, spec) for k, spec, _ in texts if k == request.POST.get('standard')), None)
    if reset:
        Text.objects.filter(key=reset[0]).delete()
        messages.success(request, f'„{reset[1].title}“ steht wieder auf dem Standard.')
        return redirect(request.path)
    if request.method == 'POST' and all(form.is_valid() for _, _, form in texts):
        with transaction.atomic():
            for text in texts:
                keep(*text)
        messages.success(request, f'„{document.title}“ gespeichert.')
        return redirect(back)
    counts, values = shared(), basics()
    return render(request, 'documents/document.html', {
        'trail': [(KINDS[kind][0], back)], 'back': back, 'document': document, 'kind': kind,
        'letter': document.letter and 'design_papers' in request.user.roles,
        'texts': [(key, spec, form, counts.get(key, ''), [(name, (values | examples(key)).get(name, '')) for name in dict.fromkeys(spec.placeholders + ALWAYS)])
                  for key, spec, form in texts],
    })


@require('word_texts', 'design_papers')
@xframe_options_sameorigin
@FRAMED
def document_preview(request, kind, key):
    """A Bescheid, a message or a page's text as it arrives, with what is typed in, unsaved: a Bescheid as the PDF its
    recipient gets, the rest as the Postfach shows it; the text being worded marked where it stands. The drafts are saved
    within a transaction undone at once, so it reads exactly as it would once saved; with nothing posted, as saved."""
    document, word = opened(request, kind, key)
    texts = text_forms(document, request.POST) if word and request.method == 'POST' else []
    if not all(form.is_valid() for _, _, form in texts):
        return render(request, 'documents/frame.html', {'errors': [e for _, _, form in texts for errors in form.errors.values() for e in errors]})
    marking = document.letter or len(document.texts) > 1  # a message of one text would be marked all over
    shown = request.POST.get('markiert') if marking and request.POST.get('markiert') in document.texts else None
    with transaction.atomic():
        for _, _, form in texts:
            form.save()
        if shown:  # the text being worded, marked wherever it stands (core.paper.marked)
            spec = TEXTS[shown]
            record = Text.objects.filter(key=shown).first() or Text(key=shown, subject=spec.subject, body=spec.body)
            record.subject, record.body = paper.marked(record.subject), paper.marked(record.body)
            record.save()
        page = letter_of(document.key) if document.letter else worded(document.key, **Values(examples(document.key)))
        made = paper.pdf(page, document.title) if document.letter else None
        transaction.set_rollback(True)
    if not document.letter:
        subject, body = page
        return render(request, 'documents/frame.html', {'subject': highlighted(subject), 'body': highlighted(mark_safe(linebreaks(body, autoescape=True)))})
    return paper.respond(made, document.title)


def highlighted(html):
    """Escaped text with what is marked (core.paper.marked) as <mark>; nothing else of it as markup."""
    return mark_safe(conditional_escape(html).replace(paper.MARK, '<mark>').replace(paper.UNMARK, '</mark>'))


def letter_of(key, **more):
    """A Bescheid, by its text's key, as the letter the made-up person gets (samples.letter); `more`: what else its
    template is given, the letter template's draft."""
    return render_to_string('core/_letter.html', {'letter': letter(key), 'institution': Institution.get()} | more)


def content(page):
    """A document's own part, as a template's text area holds it where its content is not yet the Hochschule's to write:
    (its title, the rest as HTML, what its placeholders say), without what the template draws itself or nobody prints."""
    root = lxml_html.document_fromstring(page).find_class('document')[0]
    institution = Institution.get()
    values = paper.values(institution, paper.meta_of(root, institution))
    for el in [el for el in root.iter() if isinstance(el.tag, str) and SET_BY_PAPER & set(el.get('class', '').split())]:
        el.drop_tree()
    for el in root.iter():  # lengths in pixels become points, as the PDF reads them (core.paper.px), at the editor's scale
        if isinstance(el.tag, str) and el.get('style'):
            el.set('style', PIXELS.sub(r'calc(\1 * 0.75 * var(--pt))', el.get('style')))
    heading = next(root.iter('h1', 'h2'), None)
    title = heading.text_content().strip() if heading is not None else ''
    if heading is not None:
        heading.drop_tree()
    return title, (root.text or '') + ''.join(lxml_html.tostring(el, encoding='unicode') for el in root), values


def known(key, request):
    """What the editor writes a document's content with (flow.js): each field with its value for the made-up person, each
    list with its fields and rows, the kinds of block."""
    pages, common, *_ = made_up(request)
    values, lists = compose.data(key, common | pages[key][1])
    values['amt'] = compose.plain(compose.flow(key).get('office', []), values)
    fields, columns = compose.DATA[key]
    return {
        'blocks': compose.BLOCKS, 'values': values,
        'fields': [[name, label] for name, label in fields.items()],
        'lists': {name: {'label': label, 'fields': [[col, what] for col, what in cols.items()], 'rows': lists.get(name, [])} for name, (label, cols) in columns.items()},
    }


def back_from(key):
    return reverse('documents:bescheide' if key in paper.LETTERS else 'documents:bescheinigungen')


@require('word_texts', 'design_papers')
def editor(request, key):
    """A document's template, edited as it prints: whoever designs, what stands around its content; whoever words texts,
    the content itself, written on the page where it is the Hochschule's (compose); saved together, set back to
    ConCampus's own on asking."""
    if key not in paper.DOCUMENTS:
        raise Http404
    roles, record = request.user.roles, Layout.objects.filter(key=key).first()
    may_design, may_word, composed = 'design_papers' in roles, 'word_texts' in roles, key in compose.VORSCHLAG
    title, stored = paper.DOCUMENTS[key][0], paper.template(key)
    frame, flow = {k: v for k, v in stored.items() if k != 'flow'}, compose.flow(key) if composed else None
    if request.POST.get('art') == 'standard' and record and may_design and may_word:
        record.delete()
        messages.success(request, f'„{title}“ steht wieder, wie ConCampus es vorschlägt.')
        return redirect(request.path)
    source = request.POST.get('von')
    if request.POST.get('art') == 'uebernehmen' and may_design and source in paper.DOCUMENTS and source != key:  # another's look, its own content kept
        record = record or Layout(key=key, name=title)
        record.design = {k: v for k, v in paper.template(source).items() if k != 'flow'} | ({'flow': flow} if composed else {})
        try:
            paper.clean(record.design, letter=key in paper.LETTERS, flowing=key not in paper.SET)
        except ValidationError as error:
            messages.error(request, f'Nicht übernommen: {" ".join(error.messages)}')
            return redirect(request.path)
        record.save()
        messages.success(request, f'Die Gestaltung von „{paper.DOCUMENTS[source][0]}“ übernommen; der Inhalt ist geblieben.')
        return redirect(request.path)
    if request.method == 'POST':
        try:
            posted = json.loads(request.POST.get('design', ''))
            if may_design:
                frame = paper.clean(posted, letter=key in paper.LETTERS, flowing=key not in paper.SET)
            if composed and may_word:
                flow = compose.clean(key, posted.get('flow'))
        except (ValueError, ValidationError, AttributeError) as error:
            messages.error(request, 'Nicht gespeichert: ' + ' '.join(getattr(error, 'messages', ['Die Vorlage ließ sich nicht lesen.'])))
            if isinstance(posted, dict) and isinstance(posted.get('style'), dict) and isinstance(posted.get('elements'), list):
                frame, flow = posted, posted.get('flow', flow)
        else:
            record = record or Layout(key=key, name=title)
            record.design = frame | ({'flow': flow} if composed else {})
            record.save()
            messages.success(request, f'„{title}“ gespeichert.')
            return redirect(request.path)
    others = [d for d in documents().values() if d.letter] if key in paper.LETTERS else []
    return render(request, 'documents/paper.html', {
        'menu': None,  # the window's width for the page and the rail (documents/paper.html)
        'key': key, 'name': title, 'record': record, 'back': back_from(key), 'others': others, 'shown': 'zulassung' if others else '',
        'reset': record and may_design and may_word, 'may_design': may_design,
        'sources': [(k, t) for k, (t, _) in paper.DOCUMENTS.items() if k != key and (k in paper.LETTERS) == (key in paper.LETTERS)],
        'editor': editor_data(frame, title) | {
            'letters': key in paper.LETTERS, 'flowing': key not in paper.SET, 'used': [title],
            'content': reverse('documents:paper_content', args=[key]), 'unsaved': request.method == 'POST',
            'mayDesign': may_design, 'mayWord': may_word, 'flow': flow, 'compose': known(key, request) if composed else None,
        },
    })


def editor_data(design, name):
    """What the page in the browser needs to lay the template out as the PDF will (paper.js)."""
    institution = Institution.get()
    own = paper.own_fonts()
    fonts = {key: {'name': title, 'faces': [{'w': w, 'i': i, 'url': static(f'core/fonts/{stem}-{suffix}.ttf')} for (w, i), suffix in paper.FACES.items()]}
             for key, (title, stem) in paper.FAMILIES.items()}
    fonts |= {key: {'name': title, 'own': True, 'faces': [face(art) for art in arts.values()]} for key, (title, arts) in own.items()}
    pictures = [{'pk': a.pk, 'name': a.name, 'url': reverse('documents:artwork', args=[a.pk]), 'w': a.width, 'h': a.height} for a in Artwork.objects.filter(kind=Artwork.Kind.PICTURE)]
    logo = institution.logo
    sample_values = paper.values(institution, {  # the Hochschule's own values; for a document, its person and its pages, an example
        'words': paper.GERMAN, 'office': ['Akademische und Studentische Angelegenheiten'], 'title': 'Zulassung zum Studium', 'reference': '2026-1234',
        'date': '16. November 2026', 'person': 'Erika Mustermann', 'matrikel': '100123', 'fields': {
            'person_geburtsdatum': '1. April 2001', 'person_geburtsort': 'Musterstadt', 'studium_studiengang': 'Musik', 'studium_hauptfach': 'Violine', 'studium_grad': 'Bachelor of Music',
        },
    }) | {'seitenzahl': 'Seite 1 von 2', 'seite': '1', 'seiten': '2'}
    return {
        'design': design, 'name': name, 'fonts': fonts, 'pictures': pictures, 'values': sample_values,
        'fields': [{'key': key, 'group': group, 'label': label, 'about': about, 'example': group not in (paper.HOCHSCHULE, paper.BANK)}
                   for key, (group, label, about) in paper.FIELDS.items()],
        'accent': institution.accent or '#008a00', 'wordmark': institution.lines,
        'logo': reverse('logo') if logo else '', 'svg': bool(logo) and logo.name.lower().endswith('.svg'),
        'upload': reverse('documents:artworks'),
    }


def shown_page(key, request, **more):
    """The page a template is shown with: a letter with the Bescheid asked for (?dokument=), any other its own document."""
    if key in paper.LETTERS:
        chosen = request.POST.get('dokument') or request.GET.get('dokument')
        return letter_of(chosen if chosen in TEXTS and TEXTS[chosen].group == 'Bescheide' else 'zulassung', **more)
    return sample(key, request, **more)


@require('word_texts', 'design_papers')
def paper_content(request, key):
    """The content of a document whose content is not the Hochschule's to write, as the editor's text area shows it (paper.js)."""
    if key not in paper.DOCUMENTS:
        raise Http404
    title, body, values = content(shown_page(key, request))
    return JsonResponse({'title': title, 'html': body, 'values': values})


@require('word_texts', 'design_papers')
@xframe_options_sameorigin
@FRAMED
def preview(request, key):
    """A document on its template, as the PDF: as posted from the editor, unsaved, or as kept."""
    if key not in paper.DOCUMENTS:
        raise Http404
    design, more = paper.template(key), {}
    if request.method == 'POST':
        try:
            posted = json.loads(request.POST.get('design', ''))
            design = paper.clean(posted)
            if key in compose.VORSCHLAG and posted.get('flow'):
                more['draft'] = compose.clean(key, posted['flow'])
        except (ValueError, ValidationError, AttributeError) as error:
            return render(request, 'documents/frame.html', {'errors': getattr(error, 'messages', ['Die Vorlage ließ sich nicht lesen.'])}, status=400)
    title = paper.DOCUMENTS[key][0]
    return paper.respond(paper.pdf(shown_page(key, request, **more), title, design=design), title)


def face(art):
    """A font face as the editor knows it (paper.js)."""
    return {'pk': art.pk, 'w': art.weight or 400, 'i': art.italic, 'url': reverse('documents:artwork', args=[art.pk])}


@require('design_papers')
@require_POST
def artworks(request):
    """Uploads from the editor, checked and kept. A picture (paper.accept); or the files of a font family, as many as
    it has, once whoever uploads confirms its licence allows embedding it in PDF documents: each face found in them made
    TrueType (paper.accept_font), one already there of the same family, weight and slant replaced. JSON for the editor:
    what is kept, and what was not, saying why."""
    kind, uploads = request.POST.get('art'), request.FILES.getlist('datei')
    if kind not in Artwork.Kind.values or not uploads:
        return JsonResponse({'error': 'Bitte eine Datei wählen.'}, status=400)
    if kind == Artwork.Kind.PICTURE:
        try:
            file, width, height = paper.accept(uploads[0])
        except ValidationError as error:
            return JsonResponse({'error': ' '.join(error.messages)}, status=400)
        art = Artwork.objects.create(kind=kind, name=uploads[0].name.rsplit('.', 1)[0][:80], file=file, width=width, height=height)
        return JsonResponse({'pk': art.pk, 'name': art.name, 'url': reverse('documents:artwork', args=[art.pk]), 'w': width, 'h': height})
    if not request.POST.get('lizenz'):
        return JsonResponse({'error': 'Bitte bestätigen, dass die Lizenz der Schrift das Einbetten in PDF-Dokumente erlaubt.'}, status=400)
    if len(uploads) > 24:
        return JsonResponse({'error': 'Höchstens 24 Dateien auf einmal.'}, status=400)
    kept, refused = [], []
    for upload in uploads:
        try:
            found = paper.accept_font(upload)
        except ValidationError as error:
            refused.append(f'{upload.name}: {" ".join(error.messages)}')
            continue
        for file, family, weight, italic in found:
            family = next((a.name for a in Artwork.objects.filter(kind=kind) if slugify(a.name) == slugify(family)), family)  # one family, however written
            # a new record, not the old one changed: a face is set by its record's number (paper.faces)
            Artwork.objects.filter(kind=kind, name=family, weight=weight, italic=italic).delete()
            art = Artwork.objects.create(kind=kind, name=family, weight=weight, italic=italic, licensed=True, file=file)
            kept.append({'key': paper.OWN + slugify(family), 'name': family} | face(art))
    return JsonResponse({'kept': kept, 'refused': refused}, status=200 if kept else 400)


@require('design_papers')
def artwork(request, pk):
    """A picture or font as kept, for the editor only: a signature scanned for an Urkunde is nobody else's business."""
    art = get_object_or_404(Artwork, pk=pk)
    response = FileResponse(art.file.open('rb'), content_type=CONTENT.get(art.file.name[-4:].lower(), 'application/octet-stream'))
    response['Content-Security-Policy'] = 'sandbox'
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@require('design_papers')
@require_POST
def artwork_delete(request, pk):
    """A picture or font face gone, unless a template sets it."""
    art = get_object_or_404(Artwork, pk=pk)
    family = paper.OWN + slugify(art.name)
    for layout in Layout.objects.all():
        design, style = layout.design, layout.design.get('style', {})
        if any(el.get('artwork') == art.pk for el in design.get('elements', [])) if art.kind == Artwork.Kind.PICTURE else (
            family in (style.get('font'), style.get('heading')) or any(el.get('font') == family for el in design.get('elements', []))
        ):
            return JsonResponse({'error': f'„{layout.name}“ verwendet es noch.'}, status=400)
    art.delete()
    return JsonResponse({'ok': True})
