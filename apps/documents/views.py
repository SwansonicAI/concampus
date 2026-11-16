"""Dokumente: every document ConCampus makes (catalogue), each on a page of its own: as it is printed or arrives, beside
what can be worded on it and the Briefbogen it is set on. Two rights for two kinds of people: whoever words texts
(core.texts), whoever designs Briefbögen (core.paper, core/static/core/paper.js), each seeing what the other keeps."""
import json
import re
from collections import namedtuple

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
from core.models import Artwork, Institution, Layout, Message, Person, Text
from core.text import fold
from core.texts import ALWAYS, TEXTS, Values, basics, block, examples, render as worded

from . import compose
from .catalogue import FOLLOWING, KINDS, WITHIN, documents, shared
from .models import Content
from .samples import made_up, sample

CONTENT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.ttf': 'font/ttf'}
FRAMED = csp_override(settings.SECURE_CSP | {'frame-ancestors': [CSP.SELF]})  # a preview, shown in a frame of its page and nowhere else
WORDED = {'nachrichten', 'seiten'}  # kinds with nothing but wording: for whoever words texts only
SET_BY_PAPER = {'no-print', 'letterhead', 'address', 'dated', 'actions'}  # what the Briefbogen draws itself, or nobody prints (core.paper.blocks)
PIXELS = re.compile(r'(\d+(?:\.\d+)?)px')
SAID = {'hochschule': 'Hochschule', 'hochschule_ort': 'Ort der Hochschule'}  # of the Hochschule's placeholders, those a Textfeld's wording takes


def index(request):
    return redirect('documents:bescheide')


def kinds(person):
    """The lists a person sees: whoever only designs, those of documents on paper."""
    return [kind for kind in KINDS if 'word_texts' in person.roles or kind not in WORDED]


@require('word_texts', 'design_papers')
def listing(request, kind):
    """The documents of a kind, each with what it says, its Briefbogen, and whether the Hochschule words it its own way. A
    search finds documents of every kind by their title or anything worded on them."""
    if kind not in kinds(request.user):
        raise Refused(['word_texts'])
    q = request.GET.get('q', '').strip() if 'word_texts' in request.user.roles else ''
    own, sought, found = {t.key: t for t in Text.objects.all()}, fold(q).split(), paper.layouts()
    chosen = paper.chosen(found=found)
    rows, composed = [], set(Content.objects.values_list('key', flat=True))
    for d in documents().values():
        wording = worded_on(d, found[paper_of(d, chosen)][1] if d.paper else None)
        if not (says(d, wording, own, sought) if q else d.kind == kind) or d.kind not in kinds(request.user):
            continue
        spec = TEXTS.get(d.key) if d.kind != 'bescheinigungen' else None
        rows.append((
            d, d.key in composed if d.composed else d.key in own if d.letter else any(w.key in own for w in wording),
            spec.subject if spec else 'der ganze Inhalt, Block für Block' if d.composed else ', '.join(w.title for w in wording) or 'Wortlaut je Unterlagenart',
            found[paper_of(d, chosen)][0] if d.paper else '',
        ))
    return render(request, 'documents/list.html', {
        'kind': kind, 'title': KINDS[kind][0], 'lead': KINDS[kind][1], 'q': q, 'rows': rows, 'printed': kind not in WORDED,
    })


def says(document, wording, own, sought):
    """Whether every word sought, folded, is in a document's title or anything worded on it, as the Hochschule words it."""
    said = [document.title]
    for w in wording:
        said += [own[w.key].subject, own[w.key].body] if w.key in own else [w.subject, w.body]
    said = fold(' '.join(said))
    return all(word in said for word in sought)


def paper_of(document, chosen):
    """The Briefbogen a document is set on."""
    return paper.letter_paper(chosen, document.key) if document.letter else chosen[document.paper]


def addressed(design):
    """Whether a Briefbogen has the address window a letter needs."""
    return any(el['type'] == 'address' for el in design['elements'])


def fits(document, design):
    """Whether a document may stand on a Briefbogen: a letter needs an address window; content that flows, a text area."""
    return (not document.letter or addressed(design)) and (paper.flows(design) or not document.letter and document.key in paper.SET)


Worded = namedtuple('Worded', 'key title subject body allowed slot')  # a text worded on a document, its standard subject and body; slot: a Textfeld


def worded_on(document, design=None):
    """What is worded on a document: the texts its content holds, where it flows into its Briefbogen's text area, and
    the Briefbogen's Textfelder, worded for this document (core.paper.slot_key)."""
    found = [Worded(k, TEXTS[k].title, TEXTS[k].subject, TEXTS[k].body, TEXTS[k].allowed, False) for k in document.texts] if design is None or paper.flows(design) else []
    return found + [Worded(paper.slot_key(document.paper, el), el['slot'], '', el['text'], tuple(paper.FIELDS), True) for el in paper.slots(design or {'elements': []})]


class TextForm(forms.ModelForm):
    class Meta:
        model = Text
        fields = ['subject', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 8})}

    def clean(self):
        """Lines as ConCampus splits them: a browser sends \\r\\n, and „\\n\\n“ divides the paragraphs (core.texts.filled)."""
        return {name: value.replace('\r\n', '\n') for name, value in super().clean().items()}


def text_forms(wording, data=None):
    """[(Worded, its form)], each by its key; a subject only where the text has one."""
    held = {t.key: t for t in Text.objects.filter(key__in=[w.key for w in wording])}
    found = []
    for w in wording:
        form = TextForm(data, prefix=w.key, instance=held.get(w.key) or Text(key=w.key, subject=w.subject, body=w.body))
        if not w.subject:
            del form.fields['subject']
        if w.slot:  # a line or two, named by its heading
            form.fields['body'].widget.attrs |= {'rows': max(2, w.body.count('\n') + 2), 'aria-label': w.title}
        found.append((w, form))
    return found


def keep(w, form):
    """A text saved where it changed; back on its standard, its record gone, where it says what the standard says."""
    if (form.cleaned_data.get('subject', w.subject), form.cleaned_data['body']) == (w.subject, w.body):
        if form.instance.pk:
            form.instance.delete()
    elif form.has_changed() or not form.instance.pk:
        form.save()


def chips(w, values):
    """The placeholders a text may hold, to insert: (placeholder, label, its example here). A Textfeld's are a Briefbogen's,
    by name, those the document fills."""
    if not w.slot:
        return [(name, name, values.get(name, '')) for name in dict.fromkeys(TEXTS[w.key].placeholders + ALWAYS)]
    return [
        (name, SAID.get(name, label), values[name]) for name, (group, label, _) in paper.FIELDS.items()
        if values.get(name) and (name in SAID or group in (paper.PERSON, paper.STUDIUM, paper.DOKUMENT))
    ]


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
    """One document: as it is printed or arrives, beside what can be worded on it, each text marked where other documents
    carry it too, and the Briefbogen it is set on. Saved together, a text only where it changed; back to its standard on asking."""
    document, word = opened(request, kind, key)
    design, back = 'design_papers' in request.user.roles and bool(document.paper), reverse(f'documents:{kind}')
    found = paper.layouts()
    chosen = paper.chosen(found=found)
    current = paper_of(document, chosen) if document.paper else ''
    fitting = {k: name for k, (name, layout, _) in found.items() if fits(document, layout)}
    wording = worded_on(document, found[current][1] if current else None)
    texts = text_forms(wording, request.POST or None) if word else []
    reset = next((w for w in wording if w.key == request.POST.get('standard')), None) if word else None
    if reset:
        Text.objects.filter(key=reset.key).delete()
        messages.success(request, f'„{reset.title}“ steht wieder auf dem Standard.')
        return redirect(request.path)
    own = Content.objects.filter(key=key).first() if document.composed and word else None
    if own and request.POST.get('art') == 'vorschlag':
        own.delete()
        messages.success(request, f'„{document.title}“ steht wieder auf dem Vorschlag.')
        return redirect(request.path)
    layout = request.POST.get('briefbogen') if design else None
    composition, refused = None, ''
    if request.method == 'POST' and document.composed and word:
        try:
            composition = compose.clean(key, json.loads(request.POST.get('inhalt', '')))
        except (ValueError, ValidationError) as error:
            refused = ' '.join(getattr(error, 'messages', ['Der Inhalt ließ sich nicht lesen.']))
    if request.method == 'POST' and not refused and all(form.is_valid() for _, form in texts) and (layout is None or layout in fitting):
        with transaction.atomic():
            for text in texts:
                keep(*text)
            if composition is not None:
                kept(key, composition, own)
            if layout is not None:
                set_on(document, layout, chosen)
        messages.success(request, f'„{document.title}“ gespeichert.')
        return redirect(back)
    if refused:
        messages.error(request, f'Nicht gespeichert: {refused}')
    counts, values = shared(), filled_in(document, request)
    return render(request, 'documents/document.html', {
        'trail': [(KINDS[kind][0], back)], 'back': back, 'document': document, 'kind': kind, 'word': word, 'design': design,
        'layouts': fitting, 'layout': current, 'paper_name': current and found[current][0], 'set': current and not paper.flows(found[current][1]),
        'settable': not document.letter and document.key in paper.SET, 'own': own,
        'texts': [(w, form, counts.get(w.key, ''), chips(w, values if w.slot else values | examples(w.key))) for w, form in texts],
        'composer': composer(document, request, json.loads(request.POST['inhalt']) if refused else None) if document.composed and word else None,
    })


def kept(key, composition, record):
    """A composition saved; gone, back on the Vorschlag, where it is the Vorschlag."""
    if composition == compose.VORSCHLAG[key]:
        if record:
            record.delete()
    elif record is None or record.content != composition:
        record = record or Content(key=key)
        record.content = composition
        record.save()


def composer(document, request, posted=None):
    """What the block editor needs (documents/static/documents/compose.js): the composition, each block's kind, and what
    the document knows, each placeholder with its value for the made-up person, each list with its columns'."""
    pages, common, *_ = made_up(request)
    values, lists = compose.data(document.key, common | pages[document.key][1])
    fields, columns = compose.DATA[document.key]
    found = posted or compose.composition(document.key)
    values['amt'] = compose.plain(found.get('office', ''), values)
    return {
        'composition': found, 'blocks': compose.BLOCKS,
        'fields': [[name, label, values.get(name, '')] for name, label in fields.items()],
        'lists': {name: [label, [[col, what, (lists.get(name) or [{}])[0].get(col, '')] for col, what in cols.items()]] for name, (label, cols) in columns.items()},
    }


def filled_in(document, request):
    """What the placeholders say on the made-up document: the Hochschule's, today's, and those of its kind (core.paper.FIELDS)."""
    if not document.paper:
        return basics()
    el = lxml_html.document_fromstring(page_of(document, request)).find_class('document')[0]
    return basics() | paper.values(Institution.get(), paper.meta_of(el, Institution.get()))


def set_on(document, layout, chosen):
    """The document onto a Briefbogen: kept where it differs from where it stands without a choice, a letter on that of all
    letters, any other on its standard."""
    institution = Institution.get()
    papers = {slot: k for slot, k in (institution.papers or {}).items() if slot != document.paper}
    if layout != (chosen['brief'] if document.letter else paper.DOCUMENTS[document.paper][1]):
        papers[document.paper] = layout
    institution.papers = papers
    institution.save(update_fields=['papers'])


@require('word_texts', 'design_papers')
@xframe_options_sameorigin
@FRAMED
def document_preview(request, kind, key):
    """The document as it is printed or arrives, with what is typed in, unsaved: on paper, as the PDF made from it, on the
    Briefbogen chosen; a message or a page's text as the Postfach shows it. The drafts are saved within a transaction undone
    at once, so it reads exactly as it would once saved; with nothing posted, as saved."""
    document, word = opened(request, kind, key)
    found = paper.layouts()
    current = paper_of(document, paper.chosen(found=found)) if document.paper else ''
    layout = request.POST.get('briefbogen') if request.POST.get('briefbogen') in found else current
    texts = text_forms(worded_on(document, found[current][1] if current else None), request.POST) if word and request.method == 'POST' else []
    if not all(form.is_valid() for _, form in texts):
        return render(request, 'documents/frame.html', {'errors': [e for _, form in texts for errors in form.errors.values() for e in errors]})
    shown = next((w for w in worded_on(document, found[current][1] if current else None) if w.key == request.POST.get('markiert')), None)
    more = {}
    if document.composed and word and 'inhalt' in request.POST:
        try:
            more['draft'] = compose.clean(key, json.loads(request.POST['inhalt']))
        except (ValueError, ValidationError) as error:
            return render(request, 'documents/frame.html', {'errors': getattr(error, 'messages', ['Der Inhalt ließ sich nicht lesen.'])})
        block_ = request.POST.get('markiert', '').removeprefix('block:')
        more['marked'] = int(block_) if block_.isdigit() else None
    with transaction.atomic():
        for _, form in texts:
            form.save()
        if shown and not shown.slot:  # the text being worded, marked wherever it stands (core.paper.marked)
            record = Text.objects.filter(key=shown.key).first() or Text(key=shown.key, subject=shown.subject, body=shown.body)
            record.subject, record.body = paper.marked(record.subject), paper.marked(record.body)
            record.save()
        page = page_of(document, request, **more)
        made = paper.pdf(page, document.title, design=found[layout][1], marked=shown.title if shown and shown.slot else '') if document.paper else None  # a Textfeld's drafts are read as it is set
        transaction.set_rollback(True)
    if not document.paper:
        subject, body = page
        return render(request, 'documents/frame.html', {'subject': highlighted(subject), 'body': highlighted(linebreaks(escape(body)))})
    return paper.respond(made, document.title)


def highlighted(html):
    """Escaped text with what is marked (core.paper.marked) as <mark>; nothing else of it as markup."""
    return mark_safe(conditional_escape(html).replace(paper.MARK, '<mark>').replace(paper.UNMARK, '</mark>'))


def page_of(document, request, **more):
    """The document for the made-up person, as the texts now say: a Bescheid as its letter, with what follows it where it
    applies; a Bescheinigung from its own template (samples), `more` given to it; a message or a page's text as (subject, body)."""
    values = Values(examples(document.key))
    if not document.paper:
        return worded(document.key, **values)
    if not document.letter:
        return sample(document.key, request, **more)
    for name, key in WITHIN.get(document.key, {}).items():
        values[name] = block(key, **values)
    subject, body = worded(document.key, *FOLLOWING.get(document.key, ()), **values)
    person = Person(first_name=values['vorname'], last_name=values['nachname'], address='Musterstraße 1\n12345 Musterstadt', matriculation=values['matrikelnummer'])
    letter = Message(pk=1234, person=person, subject=subject, body=body, sender=block('absender_bescheide'), template=document.key)
    return render_to_string('core/_letter.html', {'letter': letter, 'institution': Institution.get()})


def content(page):
    """A document's own part, as a Briefbogen's text area holds it: (its title, the rest as HTML, what its placeholders
    say), without what the Briefbogen draws itself or nobody prints."""
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


def printed(chosen):
    """[(Document, the Briefbogen it is set on)], of every document on paper."""
    return [(d, paper_of(d, chosen)) for d in documents().values() if d.paper]


def shown_on(key, chosen, design):
    """The document a Briefbogen is best shown with: the first set on it, else a Bescheid where it has an address window,
    an Urkunde where its text stands in the middle, else a Bescheinigung."""
    on = next((d for d, k in printed(chosen) if k == key), None)
    if on:
        return f'{on.kind}/{on.key}'
    return 'bescheide/zulassung' if addressed(design) else 'bescheinigungen/urkunde' if design['style'].get('middle') else 'bescheinigungen/immatrikulation'


@require('word_texts', 'design_papers')
def papers(request):
    """The Briefbögen, each as the first document set on it shows it, to open and lay out; and a new one from any."""
    found, design = paper.layouts(), 'design_papers' in request.user.roles
    if request.method == 'POST' and not design:
        raise Refused(['design_papers'])
    if request.method == 'POST':
        name, base = request.POST.get('name', '').strip()[:80], request.POST.get('vorlage')
        if not name or base not in found:
            messages.error(request, 'Bitte einen Namen und eine Vorlage angeben.')
            return redirect('documents:papers')
        key, n = slugify(name)[:36] or 'briefbogen', 1
        while key in found or key in paper.STANDARD or key == 'dateien':  # the uploads' address (urls.py)
            n += 1
            key = f'{slugify(name)[:33] or "briefbogen"}-{n}'
        Layout.objects.create(key=key, name=name, design=found[base][1])
        return redirect('documents:paper', key)
    chosen, changed = paper.chosen(found=found), set(Layout.objects.filter(key__in=paper.STANDARD).values_list('key', flat=True))
    on = printed(chosen)
    return render(request, 'documents/papers.html', {
        'layouts': [
            (key, name, 'eigener' if not standard else 'geändert' if key in changed else '', [d.title for d, k in on if k == key], shown_on(key, chosen, layout))
            for key, (name, layout, standard) in found.items()
        ],
        'choices': [(key, name) for key, (name, _, _) in found.items()], 'design': design,
    })


@require('design_papers')
def editor(request, key):
    """One Briefbogen, laid out on the page: saved, set back to its standard, or deleted."""
    found = paper.layouts()
    if key not in found:
        raise Http404
    name, design, standard = found[key]
    record = Layout.objects.filter(key=key).first()
    chosen = paper.chosen(found=found)
    on = [d for d, k in printed(chosen) if k == key]
    letters, flowing = any(d.letter for d in on), any(d.letter or d.key not in paper.SET for d in on)
    if request.POST.get('art') == 'standard' and standard:
        if record:
            record.delete()
        messages.success(request, f'„{paper.STANDARD[key][0]}“ steht wieder auf dem Standard.')
        return redirect('documents:paper', key)
    if request.POST.get('art') == 'loeschen' and record and not standard:
        institution = Institution.get()
        institution.papers = {slot: k for slot, k in (institution.papers or {}).items() if k != key}
        institution.save(update_fields=['papers'])
        record.delete()
        messages.success(request, f'„{record.name}“ gelöscht; was darauf stand, steht wieder auf seinem Standard.')
        return redirect('documents:papers')
    if request.method == 'POST':
        posted, name = request.POST.get('design', ''), request.POST.get('name', '').strip()[:80] or name
        try:
            design = json.loads(posted)
            cleaned = paper.clean(design, letter=letters, flowing=flowing)
        except (ValueError, ValidationError) as error:
            messages.error(request, 'Nicht gespeichert: ' + ' '.join(getattr(error, 'messages', ['Der Briefbogen ließ sich nicht lesen.'])))
            if not (isinstance(design, dict) and isinstance(design.get('style'), dict) and isinstance(design.get('elements'), list)):
                design = found[key][1]
        else:
            record = record or Layout(key=key)
            record.name, record.design = name, cleaned
            record.save()
            messages.success(request, f'„{name}“ gespeichert.')
            return redirect('documents:paper', key)
    others = [(d, k) for d, k in printed(chosen) if k != key]
    return render(request, 'documents/paper.html', {
        'menu': None,  # the window's width for the page and the rail (documents/paper.html)
        'key': key, 'name': name, 'standard': standard, 'own': bool(record), 'record': record,
        'shown': shown_on(key, chosen, design), 'on': on, 'others': [d for d, _ in others],
        'editor': editor_data(design, name) | {
            'letters': letters, 'flowing': flowing, 'used': [d.title for d in on], 'content': reverse('documents:paper_content', args=[key]),
            'unsaved': request.method == 'POST',  # a save refused: what was sent stands, still to be saved
        },
    })


def editor_data(design, name):
    """What the page in the browser needs to lay the Briefbogen out as the PDF will (paper.js)."""
    institution = Institution.get()
    own = paper.own_fonts()
    fonts = {key: {'name': title, 'faces': [{'w': w, 'i': i, 'url': static(f'core/fonts/{stem}-{suffix}.ttf')} for (w, i), suffix in paper.FACES.items()]}
             for key, (title, stem) in paper.FAMILIES.items()}
    fonts |= {key: {'name': title, 'own': True, 'faces': [face(art) for art in arts.values()]} for key, (title, arts) in own.items()}
    pictures = [{'pk': a.pk, 'name': a.name, 'url': reverse('documents:artwork', args=[a.pk]), 'w': a.width, 'h': a.height} for a in Artwork.objects.filter(kind=Artwork.Kind.PICTURE)]
    logo = institution.logo
    sample = paper.values(institution, {  # the Hochschule's own values; for a document, its person and its pages, an example
        'words': paper.GERMAN, 'office': ['Akademische und Studentische Angelegenheiten'], 'title': 'Zulassung zum Studium', 'reference': '2026-1234',
        'date': '16. November 2026', 'person': 'Erika Mustermann', 'matrikel': '100123', 'fields': {
            'person_geburtsdatum': '1. April 2001', 'person_geburtsort': 'Musterstadt', 'studium_studiengang': 'Musik', 'studium_hauptfach': 'Violine', 'studium_grad': 'Bachelor of Music',
        },
    }) | {'seitenzahl': 'Seite 1 von 2', 'seite': '1', 'seiten': '2'}
    return {
        'design': design, 'name': name, 'fonts': fonts, 'pictures': pictures, 'values': sample,
        'fields': [{'key': key, 'group': group, 'label': label, 'about': about, 'example': group not in (paper.HOCHSCHULE, paper.BANK)}
                   for key, (group, label, about) in paper.FIELDS.items()],
        'accent': institution.accent or '#008a00', 'wordmark': institution.lines,
        'logo': reverse('logo') if logo else '', 'svg': bool(logo) and logo.name.lower().endswith('.svg'),
        'upload': reverse('documents:artworks'),
    }


def chosen_document(request):
    """The document a Briefbogen is shown with (?dokument=kind/key), of those on paper."""
    kind, _, key = (request.POST.get('dokument') or request.GET.get('dokument', '')).partition('/')
    document = documents().get((kind, key))
    if document is None or not document.paper:
        raise Http404
    return document


@require('design_papers')
def paper_content(request, key):
    """The content of a document as the editor's text area shows it, in place of words standing for it (paper.js)."""
    document = chosen_document(request)
    title, body, values = content(page_of(document, request))
    slots = {t.key.partition('.')[2]: t.body for t in Text.objects.filter(key__startswith=f'{document.paper}.')}  # its Textfelder as it words them
    return JsonResponse({'title': title, 'html': body, 'values': values, 'slots': slots})


@require('word_texts', 'design_papers')
@xframe_options_sameorigin
@FRAMED
def preview(request, key):
    """A document (chosen_document) on a Briefbogen, as the PDF: as posted from the editor, unsaved, or as kept."""
    document = chosen_document(request)
    if request.method == 'POST':
        try:
            design = paper.clean(json.loads(request.POST.get('design', '')))
        except (ValueError, ValidationError) as error:
            return render(request, 'documents/frame.html', {'errors': getattr(error, 'messages', ['Der Briefbogen ließ sich nicht lesen.'])}, status=400)
    else:
        found = paper.layouts()
        if key not in found:
            raise Http404
        design = found[key][1]
    return paper.respond(paper.pdf(page_of(document, request), document.title, design=design), document.title)


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
    """A picture or font face gone, unless a Briefbogen sets it."""
    art = get_object_or_404(Artwork, pk=pk)
    family = paper.OWN + slugify(art.name)
    for name, design, _ in paper.layouts().values():
        style = design.get('style', {})
        if any(el.get('artwork') == art.pk for el in design.get('elements', [])) if art.kind == Artwork.Kind.PICTURE else (
            family in (style.get('font'), style.get('heading')) or any(el.get('font') == family for el in design.get('elements', []))
        ):
            return JsonResponse({'error': f'„{name}“ verwendet es noch.'}, status=400)
    art.delete()
    return JsonResponse({'ok': True})
