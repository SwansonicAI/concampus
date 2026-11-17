"""Dokumente: every document ConCampus makes (catalogue). One on paper is edited as it prints (core/static/core/paper.js,
flow.js): its page by whoever designs, its content by whoever words texts. A copy takes page and content from the
document it was copied from until it gets its own; whoever changes what others share says whether for all of them or
for this one alone. Messages and the texts of ConCampus's pages are written as they arrive (core/static/core/words.js)."""
import json
import re

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.csp import CSP
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_POST

from core import paper
from core.access import Refused, require
from core.models import Artwork, Institution, Text
from core.text import fold
from core.texts import NAMES, TEXTS, basics, examples, wording
from curriculum.models import Program

from . import compose
from .catalogue import KINDS, OCCASIONS, followers, free_key, papers, programs, resolved, texts
from .models import Paper
from .samples import made_up, sample

CONTENT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.ttf': 'font/ttf'}
FRAMED = csp_override(settings.SECURE_CSP | {'frame-ancestors': [CSP.SELF]})  # a preview, shown in a frame of its page and nowhere else
WORDED = {'nachrichten', 'seiten'}  # kinds with nothing but wording: for whoever words texts only
PLACEHOLDER = re.compile(r'\{(\w+)\}')


def index(request):
    return redirect('documents:bescheide')


def kinds(person):
    """The lists a person sees: whoever only designs, those of documents on paper; whoever does neither, none."""
    roles = person.roles
    return [kind for kind in KINDS if 'word_texts' in roles or 'design_papers' in roles and kind not in WORDED]


def pills(text, key):
    """A text as the editor shows it (words.js): each placeholder its example, on a tint, its name on pointing."""
    found = examples(key)
    return mark_safe(PLACEHOLDER.sub(lambda m: f'<span class="flow-pill" title="{m[1]}">{escape(found.get(m[1], m[1]))}</span>', escape(text)))


def said(p, found):
    """All a document on paper says, folded, for a search: its name and its words."""
    content = resolved(p.key, 'content', found)[0]
    return fold(' '.join([p.name] + [r.get('t', '') for b in content.get('blocks', []) for runs in compose.runs_of(b) for r in runs]))


@require('word_texts', 'design_papers')
def listing(request, kind):
    """The documents of a kind. On paper, each with what it takes from another and for whom it stands; a message or a
    page's text with what it says. A search finds documents of every kind by their name or anything written on them."""
    if kind not in kinds(request.user):
        raise Refused(['word_texts'])
    q, mine = request.GET.get('q', '').strip(), kinds(request.user)
    sought, found, own = fold(q).split(), papers(), {t.key: t for t in Text.objects.all()}
    rows = []
    for p in found.values():
        what = OCCASIONS[p.occasion][0]
        if p.key == 'brief':  # the blank letter beneath them all, not one to write: its head and foot are changed in any Bescheid
            continue
        if what in mine and (all(w in said(p, found) for w in sought) if q else what == kind):
            copy, others = p.key != p.occasion, len(followers(p.key, 'page', found))
            rows.append({
                'url': reverse('documents:document', args=[what, p.key]), 'name': p.name, 'kind': what, 'changed': bool(p.pk), 'copy': copy,
                'note': f'Kopie von „{found[p.follows].name}“' if copy and p.follows in found else f'Kopf und Fuß auch für {others} weitere' if others else '',
                'programs': ', '.join(x.title for x in programs(p)),
            })
    for t in texts().values():
        spec = TEXTS[t.key]
        text = own.get(t.key) or spec
        if t.kind in mine and (all(w in fold(f'{t.title} {text.subject} {text.body}') for w in sought) if q else t.kind == kind):
            rows.append({'url': reverse('documents:document', args=[t.kind, t.key]), 'name': t.title, 'kind': t.kind, 'changed': t.key in own,
                         'about': pills(text.subject, t.key)})
    return render(request, 'documents/list.html', {
        'kind': kind, 'title': KINDS[kind][0], 'lead': KINDS[kind][1], 'q': q, 'rows': rows, 'paper': kind not in WORDED,
        'copies': any(row.get('copy') for row in rows),  # for whom: said only where a copy stands for some
        'sources': [(p.key, p.name) for p in found.values() if OCCASIONS[p.occasion][0] == kind],
    })


class TextForm(forms.ModelForm):
    class Meta:
        model = Text
        fields = ['subject', 'body']

    def clean(self):
        """Lines as ConCampus splits them: a browser sends \\r\\n, and „\\n\\n“ divides the paragraphs (core.texts.filled)."""
        return {name: value.replace('\r\n', '\n') for name, value in super().clean().items()}


def text_forms(keys, data=None):
    """[(key, Spec, its form)] for each text; a subject only where the text has one."""
    held = {t.key: t for t in Text.objects.filter(key__in=keys)}
    found = []
    for key in keys:
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


@require('word_texts', 'design_papers')
def document(request, kind, key):
    """One document. On paper: edited as it prints (editor). A message or a page's text: written as it arrives, saved
    where it changed, back to its standard on asking."""
    if kind not in kinds(request.user):
        raise Refused(['word_texts'])
    if kind not in WORDED:
        return editor(request, kind, key)
    text = texts().get((kind, key))
    if text is None:
        raise Http404
    back = reverse(f'documents:{kind}')
    found = text_forms([key], request.POST or None)
    if request.POST.get('standard') == key:
        Text.objects.filter(key=key).delete()
        messages.success(request, f'„{text.title}“ steht wieder, wie ConCampus es vorschlägt.')
        return redirect(request.path)
    if request.method == 'POST' and all(form.is_valid() for _, _, form in found):
        for item in found:
            keep(*item)
        messages.success(request, f'„{text.title}“ gespeichert.')
        return redirect(back)
    spec, values = TEXTS[key], basics() | examples(key)
    return render(request, 'documents/document.html', {
        'trail': [(KINDS[kind][0], back)], 'back': back, 'document': text,
        'words': {key: {'values': values, 'labels': {name: NAMES.get(name, name) for name in spec.allowed}}},
        'texts': found,
    })


# ---- documents on paper ---------------------------------------------------------------------------------------------

def edited(request, key):
    """The document asked for, of the kind in the URL, from all there are."""
    found = papers()
    if key not in found:
        raise Http404
    return found[key], found


def freeze(key, part, value, found):
    """Whatever takes `part` from this document keeps it as it is, as its own: before it changes or goes."""
    for p in followers(key, part, found):
        setattr(p, part, value)
        p.save()


def known(p, found, request):
    """What the editor writes a document's content with (flow.js): each value with what it says, whose it is and its value
    for the made-up person; each list with its fields and rows; the styles; the Hochschule's own texts among the values,
    to word where they are used."""
    occasion = p.occasion
    if occasion in compose.DATA and occasion != 'brief':
        pages, common, *_ = made_up(request)
        values, lists = compose.data(occasion, common | pages[occasion][1])
    elif occasion == 'brief':
        from .samples import letter

        values, lists = compose.data('brief', {'letter': letter('brief')})
    else:
        values, lists = compose.letter_values(occasion, examples(occasion)), {}
    fields, columns = compose.fields_of(occasion)
    values['amt'] = resolved(p.key, 'content', found)[0].get('office') or values.get('hochschule', '')
    uses = lambda field: sum(1 for q in found.values() if q.occasion != 'brief' and any(r.get('f') == field for b in resolved(q.key, 'content', found)[0].get('blocks', [])
                                                                                     for runs in compose.runs_of(b) for r in runs))
    elsewhere = {}  # on the Brief: what each message brings, shown as where it goes, worded where the message is
    if occasion == 'brief':
        values |= {'betreff': fields['betreff'], 'text': fields['text']}
        own = {'said': 'Jede Nachricht bringt ihren eigenen mit.', 'url': reverse('documents:nachrichten'), 'link': 'Zu den Nachrichten'}
        elsewhere = {'betreff': own, 'text': own, 'absender': {'said': 'Das Amt, das die jeweilige Nachricht schickt; hier ein Beispiel.'}}
    return {
        'styles': compose.STYLES, 'values': values,
        'fields': [[name, label, compose.source(name, occasion)] for name, label in fields.items()],
        'lists': {name: {'label': label, 'fields': [[col, what] for col, what in cols.items()], 'rows': lists.get(name, [])} for name, (label, cols) in columns.items()},
        'worded': {field: {
            'text': text, 'title': TEXTS[text].title, 'body': wording(text)[1], 'uses': uses(field),
            'values': basics() | examples(text), 'labels': {name: NAMES.get(name, name) for name in TEXTS[text].allowed},
        } for field, text in compose.WORDED.items() if field in fields and occasion != 'brief'},  # the Brief's Absender is each message's own
        'elsewhere': elsewhere,
    }


def shared(key, part, found):
    """What the editor says of a part: whose it is, and how many others have it."""
    owner = resolved(key, part, found)[1]
    return {'own': owner.key == key, 'from': owner.name, 'count': len(followers(owner.key, part, found))}  # the others: its followers, or the one it takes it from and its other followers


@require('word_texts', 'design_papers')
def editor(request, kind, key):
    """A document on paper, edited as it prints: whoever designs, its page; whoever words texts, its content and the
    Hochschule's texts in it. Saved together; what others take from it, or it from another, changed for all of them or
    for this one alone, as asked. Copied, deleted where it is a copy, or set back to ConCampus's own."""
    p, found = edited(request, key)
    if OCCASIONS[p.occasion][0] != kind:
        raise Http404
    roles = request.user.roles
    may_design, may_word = 'design_papers' in roles, 'word_texts' in roles
    page, content = resolved(key, 'page', found)[0], resolved(key, 'content', found)[0]
    act = request.POST.get('art')
    if act == 'standard' and p.pk and key == p.occasion and may_design and may_word:
        p.delete()
        messages.success(request, f'„{p.name}“ steht wieder, wie ConCampus es vorschlägt.')
        return redirect(request.path)
    if act == 'loeschen' and p.pk and key != p.occasion and may_design and may_word:
        with transaction.atomic():
            freeze(key, 'page', page, found)
            freeze(key, 'content', content, found)
            p.delete()
        messages.success(request, f'„{p.name}“ gelöscht.')
        return redirect('documents:' + kind)
    if request.method == 'POST':
        try:
            posted = json.loads(request.POST.get('design', ''))
            scope = posted.get('scope') or {}
            new_page = paper.clean(posted['page'], letter=compose.letter(p.occasion)) if may_design else page
            new_content = compose.clean(p.occasion, posted['content']) if may_word else content
            worded = text_forms([t for t in (posted.get('texts') or {}) if t in compose.WORDED.values()], {
                f'{t}-body': body for t, body in (posted.get('texts') or {}).items()}) if may_word else []
            for _, _, form in worded:
                if not form.is_valid():
                    raise ValidationError([e for errors in form.errors.values() for e in errors])
        except (KeyError, TypeError, ValueError, ValidationError, AttributeError) as error:
            messages.error(request, 'Nicht gespeichert: ' + ' '.join(getattr(error, 'messages', ['Das Dokument ließ sich nicht lesen.'])))
            return redirect(request.path)
        name, chosen = str(posted.get('name') or p.name)[:120], posted.get('programs')
        with transaction.atomic():
            changed = name != p.name
            was = {'page': paper.clean(page, letter=compose.letter(p.occasion)) if may_design else page,  # as the editor sends it back untouched
                   'content': compose.clean(p.occasion, content) if may_word else content}
            for part, new, old in (('page', new_page, page), ('content', new_content, content)):
                if new == was[part]:
                    continue
                owner = resolved(key, part, found)[1]
                if owner.key != key and scope.get(part) == 'alle':  # what it takes from another, changed there, for all
                    setattr(owner, part, new)
                    owner.save()
                    continue
                if owner.key == key and scope.get(part) != 'alle':  # its own, changed for it alone: the others keep it as it was
                    freeze(key, part, old, found)
                setattr(p, part, new)
                changed = True
            p.name = name
            if changed:
                p.save()
            if p.pk and key != p.occasion and may_word and isinstance(chosen, list):
                p.programs.set(Program.objects.filter(pk__in=[x for x in chosen if isinstance(x, int)]))
            for item in worded:
                keep(*item)
        messages.success(request, f'„{p.name}“ gespeichert.')
        return redirect(request.path)
    return render(request, 'documents/paper.html', {
        'menu': None,  # the window's width for the page and the rail (documents/paper.html)
        'key': key, 'kind': kind, 'name': p.name, 'record': p if p.pk else None, 'back': reverse('documents:' + kind),
        'reset': p.pk and key == p.occasion and may_design and may_word, 'copy': key != p.occasion and p.pk and may_design and may_word,
        'editor': editor_data(page, p.name) | {
            'letters': compose.letter(p.occasion), 'unsaved': False, 'mayDesign': may_design, 'mayWord': may_word, 'flow': content,
            'compose': known(p, found, request), 'shared': {'page': shared(key, 'page', found), 'content': shared(key, 'content', found)},
            'copy': key != p.occasion, 'programs': [[x.pk, x.title] for x in Program.objects.all()], 'chosen': [x.pk for x in programs(p)],
            'occasion': OCCASIONS[p.occasion][1],
            'settings': reverse('setup:list', args=['hochschule']) if 'manage_institution' in request.user.roles else '',  # where the Hochschule's own values change
        },
    })


@require('word_texts', 'design_papers')
@require_POST
def copy(request, kind):
    """A new document, as another (`von`) until it is changed: for the same occasion, under its own name."""
    p, _ = edited(request, request.POST.get('von', ''))
    if kind not in kinds(request.user) or OCCASIONS[p.occasion][0] != kind:
        raise Http404
    name = (request.POST.get('name', '').strip() or f'{p.name}, Kopie')[:120]
    made = Paper.objects.create(key=free_key(name), occasion=p.occasion, name=name, follows=p.key)
    messages.success(request, f'„{made.name}“ angelegt: wie „{p.name}“, bis Sie es ändern.')
    return redirect('documents:document', kind, made.key)


def editor_data(design, name):
    """What the page in the browser needs to lay the document out as the PDF will (paper.js)."""
    institution = Institution.get()
    own = paper.own_fonts()
    fonts = {key: {'name': title, 'faces': [{'w': w, 'i': i, 'url': static(f'core/fonts/{stem}-{suffix}.ttf')} for (w, i), suffix in paper.FACES.items()]}
             for key, (title, stem) in paper.FAMILIES.items()}
    fonts |= {key: {'name': title, 'own': True, 'faces': [face(art) for art in arts.values()]} for key, (title, arts) in own.items()}
    pictures = [{'pk': a.pk, 'name': a.name, 'url': reverse('documents:artwork', args=[a.pk]), 'w': a.width, 'h': a.height} for a in Artwork.objects.filter(kind=Artwork.Kind.PICTURE)]
    logo = institution.logo
    sample_values = paper.values(institution, {  # the Hochschule's own values; for a document, its person and its pages, an example
        'words': paper.GERMAN, 'office': ['Akademische und Studentische Angelegenheiten'], 'title': name, 'reference': '2026-1234',
        'date': compose.day(timezone.localdate()), 'person': 'Erika Mustermann', 'matrikel': '100123',
    }) | {'seitenzahl': 'Seite 1 von 2', 'seite': '1', 'seiten': '2'}
    return {
        'design': design, 'name': name, 'fonts': fonts, 'pictures': pictures, 'values': sample_values,
        'fields': [{'key': key, 'group': group, 'label': label, 'about': about, 'example': group not in (paper.HOCHSCHULE, paper.BANK)}
                   for key, (group, label, about) in paper.FIELDS.items()],
        'accent': institution.accent or '#008a00', 'wordmark': institution.lines,
        'logo': reverse('logo') if logo else '', 'svg': bool(logo) and logo.name.lower().endswith('.svg'),
        'upload': reverse('documents:artworks'),
    }


@require('word_texts', 'design_papers')
@xframe_options_sameorigin
@FRAMED
def preview(request, kind, key):
    """A document as its PDF, for the made-up person: as posted from the editor, unsaved, the Hochschule's texts in it as
    drafted too, saved within a transaction undone at once; else as kept."""
    p, found = edited(request, key)
    design, draft = resolved(key, 'page', found)[0], None
    with transaction.atomic():
        if request.method == 'POST':
            try:
                posted = json.loads(request.POST.get('design', ''))
                design, draft = paper.clean(posted['page'], letter=compose.letter(p.occasion)), compose.clean(p.occasion, posted['content'])
                for _, _, form in text_forms([t for t in (posted.get('texts') or {}) if t in compose.WORDED.values()], {
                        f'{t}-body': body for t, body in (posted.get('texts') or {}).items()}):
                    if form.is_valid():
                        form.save()
            except (KeyError, TypeError, ValueError, ValidationError, AttributeError) as error:
                return render(request, 'documents/frame.html', {'errors': getattr(error, 'messages', ['Das Dokument ließ sich nicht lesen.'])}, status=400)
        made = paper.pdf(sample(p.occasion, request, shown=key, draft=draft), p.name, design=design)
        transaction.set_rollback(True)
    return paper.respond(made, p.name)


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
    """A picture or font face gone, unless a document's page sets it."""
    art = get_object_or_404(Artwork, pk=pk)
    family = paper.OWN + slugify(art.name)
    for p in Paper.objects.exclude(page=None):
        style = p.page.get('style', {})
        if any(el.get('artwork') == art.pk for el in p.page.get('elements', [])) if art.kind == Artwork.Kind.PICTURE else (
            family in (style.get('font'), style.get('heading')) or any(el.get('font') == family for el in p.page.get('elements', []))
        ):
            return JsonResponse({'error': f'„{p.name}“ verwendet es noch.'}, status=400)
    art.delete()
    return JsonResponse({'ok': True})
