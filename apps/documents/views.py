"""Dokumente: every document ConCampus makes (catalogue). One on paper is written as it prints (core/static/core/paper.js,
flow.js), by whoever words texts, on the Vorlage it stands on; the Vorlagen, a handful for all documents, are laid
out by whoever designs, in the same editor. Messages and the texts of ConCampus's pages are written as they arrive
(core/static/core/words.js)."""
from copy import deepcopy
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
from core.access import require
from core.models import Artwork, Institution, Text
from core.text import fold
from core.texts import NAMES, TEXTS, basics, examples, wording
from curriculum.models import Program

from . import compose
from .catalogue import FREE, KINDS, OCCASIONS, SENT, WHEN, free_key, letterhead_of, letterheads, on, papers, programs, standard, texts, variant, where
from .models import Letterhead, Paper
from .samples import made_up, sample

CONTENT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.ttf': 'font/ttf'}
FRAMED = csp_override(settings.SECURE_CSP | {'frame-ancestors': [CSP.SELF]})  # a preview, shown in a frame of its page and nowhere else
WORDED = {'nachrichten', 'seiten'}  # kinds with nothing but wording: for whoever words texts only
PLACEHOLDER = re.compile(r'\{(\w+)\}')


@require('word_texts', 'design_papers')
def index(request):
    """Where documents begin: in the order a Hochschule sets them up, the Hochschule's own data, the Vorlagen, then
    each list, how many it holds and how many the Hochschule has made its own."""
    roles, found, own = request.user.roles, papers(), set(Text.objects.values_list('key', flat=True))
    steps = []
    if 'manage_institution' in roles:
        steps.append({'title': 'Hochschule', 'url': reverse('setup:list', args=['hochschule']), 'about': 'Name, Logo oder Schriftzug, Anschrift und Bankverbindung, für Kopf und Fuß aller Dokumente', 'count': None})
    if 'design_papers' in roles:
        heads = letterheads()
        steps.append({'title': 'Vorlagen', 'url': reverse('documents:letterheads'), 'about': 'Kopf, Fuß und Schrift, einmal für alle Dokumente darauf',
                      'count': len(heads), 'own': sum(1 for h in heads.values() if h.pk)})
    for kind in kinds(request.user):
        if kind in WORDED:
            keys = [t.key for t in texts().values() if t.kind == kind]
            count, mine = len(keys), sum(1 for key in keys if key in own)
        else:
            listed = [p for p in found.values() if OCCASIONS[p.occasion][0] == kind and p.key != 'brief']
            count, mine = len(listed), sum(1 for p in listed if p.pk)
        steps.append({'title': KINDS[kind][0], 'url': reverse(f'documents:{kind}'), 'about': KINDS[kind][1], 'count': count, 'own': mine})
    return render(request, 'documents/index.html', {'steps': steps, 'search': 'word_texts' in roles} | (new(request, '', found) if 'word_texts' in roles else {}))


def kinds(person):
    """The lists of documents a person sees: all for whoever words texts, none for anyone else."""
    return list(KINDS) if 'word_texts' in person.roles else []


def pills(text, key):
    """A text as the editor shows it (words.js): each placeholder its example, on a tint, its name on pointing."""
    found = examples(key)
    return mark_safe(PLACEHOLDER.sub(lambda m: f'<span class="flow-pill" title="{m[1]}">{escape(found.get(m[1], m[1]))}</span>', escape(text)))


def said(p):
    """All a document on paper says, folded, for a search: its name and its words."""
    return fold(' '.join([p.name] + [r.get('t', '') for b in p.content.get('blocks', []) for runs in compose.runs_of(b) for r in runs]))


@require('word_texts')
def listing(request, kind):
    """The documents of a kind. On paper, each with its Vorlage and for whom it stands; a message or a page's text
    with what it says. A search finds documents of every kind by their name or anything written on them."""
    q, mine = request.GET.get('q', '').strip(), kinds(request.user)
    sought, found, heads, own = fold(q).split(), papers(), letterheads(), {t.key: t for t in Text.objects.all()}
    rows = []
    for p in found.values():
        what = OCCASIONS[p.occasion][0]
        if p.key == 'brief':  # the blank letter beneath them all, each message's own words on the Brief
            continue
        if all(w in said(p) for w in sought) if q else what == kind:
            head = letterhead_of(p, heads)
            rows.append({
                'url': reverse('documents:document', args=[what, p.key]), 'name': p.name, 'kind': what, 'changed': bool(p.pk) and p.key == p.occasion, 'copy': variant(p),  # „eigen“: ConCampus's, as the Hochschule changed it
                'note': f'Variante von „{found[p.occasion].name}“' if variant(p) else '', 'programs': ', '.join(x.title for x in programs(p)),
                'head': head.name,
                'when': WHEN.get(p.occasion, ''), 'where': where(p.occasion, request.user), 'gone': bool(p.pk) and p.key != p.occasion,  # a variant, a letter of the Hochschule's own
            })
    for t in texts().values():
        spec = TEXTS[t.key]
        text = own.get(t.key) or spec
        if t.kind in mine and (all(w in fold(f'{t.title} {text.subject} {text.body}') for w in sought) if q else t.kind == kind):
            rows.append({'url': reverse('documents:document', args=[t.kind, t.key]), 'name': t.title, 'kind': t.kind, 'changed': t.key in own,
                         'about': pills(text.subject, t.key), 'when': SENT[t.key], 'where': where(t.key, request.user)})
    return render(request, 'documents/list.html', {
        'kind': kind, 'title': KINDS[kind][0], 'lead': KINDS[kind][1], 'q': q, 'rows': rows, 'paper': kind not in WORDED,
        'copies': any(row.get('copy') for row in rows),  # for whom: said only where a variant stands for some
        'when': {'seiten': 'Wo', 'bescheinigungen': 'Wann ConCampus es ausstellt'}.get(kind, 'Wann ConCampus es schickt'), 'own': kind == 'eigene',
    } | new(request, kind, found))


def new(request, kind, found):
    """What „Neues Dokument“ offers on a list: one of the Hochschule's own, on any Vorlage; a variant of one of
    ConCampus's documents of the list; for messages, the way to write to students."""
    return {
        'heads': [(key, h.name) for key, h in letterheads().items()], 'head': 'bescheinigung' if kind == 'bescheinigungen' else 'brief',  # the one the list's own mostly stand on
        'sources': [(p.key, p.name) for p in found.values() if OCCASIONS[p.occasion][0] == kind and p.key == p.occasion and p.key != 'brief'],
        'compose_url': reverse('students:list') if kind == 'nachrichten' and 'message_students' in request.user.roles else '',
    }


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


@require('word_texts')
def document(request, kind, key):
    """One document. On paper: written as it prints (editor). A message or a page's text: written as it arrives, saved
    where it changed, back to its standard on asking."""
    if kind not in KINDS:
        raise Http404
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
        'trail': [(KINDS[kind][0], back)], 'back': back, 'document': text, 'when': SENT[key], 'where': where(key, request.user),
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


def known(p, found, request):
    """What the editor writes a document's content with (flow.js): each value with what it says, whose it is and its value
    for the made-up person; each list with its fields and rows; the styles; the Hochschule's own texts among the values,
    to word where they are used."""
    occasion = p.occasion
    if occasion == FREE:
        values, lists = compose.letter_values(occasion, examples('bewerbung')), {}
    elif occasion in compose.DATA and occasion != 'brief':
        pages, common, *_ = made_up(request)
        values, lists = compose.data(occasion, common | pages[occasion][1])
    elif occasion == 'brief':
        from .samples import letter

        values, lists = compose.data('brief', {'letter': letter('brief')})
    else:
        values, lists = compose.letter_values(occasion, examples(occasion)), {}
    fields, columns = compose.fields_of(occasion)
    values['amt'] = p.content.get('office') or values.get('hochschule', '')
    uses = lambda field: sum(1 for q in found.values() if q.occasion != 'brief' and any(r.get('f') == field for b in q.content.get('blocks', [])
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


def fitting(p, heads):
    """The Vorlagen a document may stand on: a letter only on one with an address window."""
    letter = compose.letter(p.occasion)
    return {key: h for key, h in heads.items() if not letter or any(el['type'] == 'address' for el in h.page['elements'])}


def posted_texts(posted):
    """The forms of the Hochschule's own texts the editor sends, worded where they stand; ValidationError where one is wrong."""
    sent = posted.get('texts') or {}
    found = text_forms([t for t in sent if t in compose.WORDED.values()], {f'{t}-body': body for t, body in sent.items()})
    for _, _, form in found:
        if not form.is_valid():
            raise ValidationError([e for errors in form.errors.values() for e in errors])
    return found


UNREAD = (KeyError, TypeError, ValueError, ValidationError, AttributeError)  # what a page or content the editor sends may fail with


def refused(request, error):
    messages.error(request, 'Nicht gespeichert: ' + ' '.join(getattr(error, 'messages', ['Das ließ sich nicht lesen.'])))
    return redirect(request.path)


@require('word_texts')
def editor(request, kind, key):
    """A document on paper, written as it prints on its Vorlage: its content and the Hochschule's texts in it, which
    Vorlage it stands on, for whom a variant stands. Copied; deleted where it is a variant or a letter of the
    Hochschule's own; set back to ConCampus's own where it is ConCampus's, which belongs to an event and stays."""
    p, found = edited(request, key)
    if OCCASIONS[p.occasion][0] != kind:
        raise Http404
    heads = fitting(p, letterheads())
    act = request.POST.get('art')
    if act == 'standard' and p.pk and key == p.occasion:
        p.delete()
        messages.success(request, f'„{p.name}“ steht wieder, wie ConCampus es vorschlägt.')
        return redirect(request.path)
    if act == 'loeschen' and p.pk and key != p.occasion:
        p.delete()
        messages.success(request, f'„{p.name}“ gelöscht.')
        return redirect('documents:' + kind)
    if request.method == 'POST':
        try:
            posted = json.loads(request.POST.get('design', ''))
            content, worded = compose.clean(p.occasion, posted['content']), posted_texts(posted)
        except UNREAD as error:
            return refused(request, error)
        head = posted.get('letterhead') if posted.get('letterhead') in heads else letterhead_of(p, heads).key
        new = (str(posted.get('name') or p.name)[:120], content, '' if head == standard(p.occasion) else head)
        with transaction.atomic():
            if new != (p.name, compose.clean(p.occasion, p.content), p.letterhead):  # as the editor sends it back untouched: nothing to keep
                p.name, p.content, p.letterhead = new
                p.save()
            if p.pk and variant(p) and isinstance(chosen := posted.get('programs'), list):
                p.programs.set(Program.objects.filter(pk__in=[x for x in chosen if isinstance(x, int)]))
            for item in worded:
                keep(*item)
        messages.success(request, f'„{p.name}“ gespeichert.')
        return redirect(request.path)
    head, free = letterhead_of(p, heads), p.occasion == FREE
    return render(request, 'documents/paper.html', {
        'menu': None,  # the window's width for the page and the rail (documents/paper.html)
        'name': p.name, 'back': reverse('documents:' + kind), 'tabs': [('first', 'Dokument'), ('pdf', 'Vorschau')],
        'preview': reverse('documents:preview', args=[kind, key]), 'copy': reverse('documents:copy', args=[kind]), 'key': key,
        'copying': '' if key == 'brief' else 'Kopie anlegen' if free else 'Variante für Studiengänge anlegen',  # the blank letter beneath every message: none
        'history': reverse('history', args=['documents.paper', p.pk]) if p.pk else '',
        'editor': editor_data(head.page, p.name) | {
            'letters': compose.letter(p.occasion), 'unsaved': False, 'mayDesign': False, 'mayWord': True, 'flow': p.content,
            'compose': known(p, found, request), 'letterhead': head.key,
            'letterheads': {k: {'name': h.name, 'page': h.page, 'url': reverse('documents:letterhead', args=[k])} for k, h in heads.items()},
            'designs': 'design_papers' in request.user.roles,
            'copy': variant(p), 'programs': [[x.pk, x.title] for x in Program.objects.all()], 'chosen': [x.pk for x in programs(p)],
            'occasion': found[p.occasion].name if variant(p) else '', 'when': '' if free else WHEN.get(p.occasion, ''), 'where': where(p.occasion, request.user), 'free': free,
            'gone': f'„{p.name}“ löschen?' if p.pk and key != p.occasion else '',  # a variant, a letter of the Hochschule's own
            'reset': bool(p.pk and key == p.occasion),
            'kept': 'Nicht zu löschen: ConCampus schickt es von selbst, wenn es so weit ist.' if key == p.occasion else '',  # ConCampus's: it belongs to an event
            'compose_url': f'{reverse("students:list")}?dokument={key}' if free and p.pk and 'message_students' in request.user.roles else '',  # the students to tick, this chosen to send
            'settings': reverse('setup:list', args=['hochschule']) if 'manage_institution' in request.user.roles else '',  # where the Hochschule's own values change
        },
    })


@require('word_texts')
@require_POST
def copy(request, kind):
    """A new document: a letter of the Hochschule's own, blank to write on; or a copy of another (`von`), for the same
    occasion, on the same Vorlage, a variant for some Studiengänge where it is ConCampus's."""
    name = request.POST.get('name', '').strip()[:120]
    if kind == 'eigene' and not request.POST.get('von'):  # on the Vorlage chosen, begun as a letter where it has an address window
        heads = letterheads()
        head = heads.get(request.POST.get('vorlage')) or heads['brief']
        letter = any(el['type'] == 'address' for el in head.page['elements'])
        name = name or ('Neuer Brief' if letter else {'bescheinigung': 'Neue Bescheinigung', 'urkunde': 'Neue Urkunde'}.get(head.key, 'Neues Dokument'))
        made = Paper.objects.create(key=free_key(name), occasion=FREE, name=name, letterhead='' if head.key == 'brief' else head.key,
                                    content=deepcopy(compose.vorschlag(FREE if letter else 'frei_seite')))
        messages.success(request, f'„{made.name}“ angelegt. In den Text klicken und schreiben.')
        return redirect('documents:document', kind, made.key)
    p, _ = edited(request, request.POST.get('von', ''))
    if OCCASIONS[p.occasion][0] != kind or p.key == 'brief':
        raise Http404
    name = name or f'{p.name}, {"Kopie" if p.occasion == FREE else "Variante"}'
    made = Paper.objects.create(key=free_key(name), occasion=p.occasion, name=name, letterhead=p.letterhead, content=deepcopy(p.content))
    messages.success(request, f'„{made.name}“ angelegt, als Kopie von „{p.name}“.' if p.occasion == FREE else
                     f'„{made.name}“ angelegt. Unter „Gilt für“ die Studiengänge wählen, für die es statt „{p.name}“ gilt.')
    return redirect('documents:document', kind, made.key)


# ---- Vorlagen -----------------------------------------------------------------------------------------------------

def head_of(key):
    """The Vorlage asked for, all there are, and all documents."""
    heads = letterheads()
    if key not in heads:
        raise Http404
    return heads[key], heads, papers()


def shown_on(h, found, heads):
    """The document a Vorlage's preview shows on it: the first of ConCampus's own that stands on it, a Bescheid before
    the blank letter; none on it yet, the first Bescheinigung."""
    standing = sorted(on(h, found, heads), key=lambda p: (p.occasion == 'brief', p.key != p.occasion))  # ConCampus's own before a variant or letter of the Hochschule's
    return standing[0] if standing else found['immatrikulationsbescheinigung']


@require('design_papers')
def letterheads_list(request):
    """The Vorlagen, each with the documents that stand on it."""
    heads, found = letterheads(), papers()
    rows = [{'url': reverse('documents:letterhead', args=[key]), 'name': h.name, 'changed': bool(h.pk), 'gone': bool(h.pk) and key not in paper.STANDARD,
             'documents': [p.name for p in on(h, found, heads) if p.key != 'brief']} for key, h in heads.items()]
    return render(request, 'documents/letterheads.html', {'rows': rows, 'sources': [(key, h.name) for key, h in heads.items()]})


@require('design_papers')
def letterhead(request, key):
    """A Vorlage, laid out as it prints, shown with a document on it: head and foot, text areas and type, for every
    document that stands on it. Copied, deleted where the Hochschule made it, or set back to ConCampus's own."""
    h, heads, found = head_of(key)
    standing = [p for p in on(h, found, heads) if p.key != 'brief']
    letter = any(compose.letter(p.occasion) for p in on(h, found, heads))  # a letter stands on it: it keeps its address window
    act = request.POST.get('art')
    if act == 'standard' and h.pk and key in paper.STANDARD:
        h.delete()
        messages.success(request, f'„{h.name}“ steht wieder, wie ConCampus es vorschlägt.')
        return redirect(request.path)
    if act == 'loeschen' and h.pk and key not in paper.STANDARD:
        h.delete()
        messages.success(request, f'„{h.name}“ gelöscht.')
        return redirect('documents:letterheads')
    if request.method == 'POST':
        try:
            posted = json.loads(request.POST.get('design', ''))
            page = paper.clean(posted['page'], letter=letter)
        except UNREAD as error:
            return refused(request, error)
        name = str(posted.get('name') or h.name)[:120]
        if (name, page) != (h.name, paper.clean(h.page, letter=letter)):
            h.name, h.page = name, page
            h.save()
        messages.success(request, f'„{h.name}“ gespeichert.')
        return redirect(request.path)
    sample = shown_on(h, found, heads)
    return render(request, 'documents/paper.html', {
        'menu': None, 'name': h.name, 'back': reverse('documents:letterheads'),
        'tabs': [('first', 'Erste Seite'), ('later', 'Folgeseiten'), ('pdf', 'Vorschau')],
        'preview': reverse('documents:letterhead_preview', args=[key]), 'copy': reverse('documents:letterhead_copy'), 'key': key, 'copying': 'Kopie anlegen',
        'history': reverse('history', args=['documents.letterhead', h.pk]) if h.pk else '',
        'editor': editor_data(h.page, h.name) | {
            'letters': letter, 'unsaved': False, 'mayDesign': True, 'mayWord': False, 'flow': sample.content,
            'compose': known(sample, found, request), 'sample': sample.name,
            'gone': h.pk and key not in paper.STANDARD and f'„{h.name}“ löschen?{" Was darauf steht, kommt auf die Vorlage seines Anlasses." if standing else ""}',
            'reset': bool(h.pk and key in paper.STANDARD), 'kept': 'Nicht zu löschen: eine Vorlage von ConCampus.' if key in paper.STANDARD else '',
            'standing': [[p.name, reverse('documents:document', args=[OCCASIONS[p.occasion][0], p.key])] for p in standing],
            'settings': reverse('setup:list', args=['hochschule']) if 'manage_institution' in request.user.roles else '',
        },
    })


@require('design_papers')
@require_POST
def letterhead_copy(request):
    """A new Vorlage, a copy of another (`von`), under its own name."""
    h, *_ = head_of(request.POST.get('von', ''))
    name = (request.POST.get('name', '').strip() or f'{h.name}, Kopie')[:120]
    made = Letterhead.objects.create(key=free_key(name, Letterhead, paper.STANDARD), name=name, page=deepcopy(h.page))
    messages.success(request, f'„{made.name}“ angelegt, als Kopie von „{h.name}“.')
    return redirect('documents:letterhead', made.key)


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


@require('word_texts')
@xframe_options_sameorigin
@FRAMED
def preview(request, kind, key):
    """A document as its PDF, for the made-up person: as posted from the editor, unsaved, on the Vorlage chosen there,
    the Hochschule's texts in it as drafted too, saved within a transaction undone at once; else as kept."""
    p, found = edited(request, key)
    heads = fitting(p, letterheads())
    design, draft = letterhead_of(p, heads).page, None
    with transaction.atomic():
        if request.method == 'POST':
            try:
                posted = json.loads(request.POST.get('design', ''))
                draft = compose.clean(p.occasion, posted['content'])
                design = heads[posted['letterhead']].page if posted.get('letterhead') in heads else design
                for _, _, form in posted_texts(posted):
                    form.save()
            except UNREAD as error:
                return framed_errors(request, error)
        made = paper.pdf(sample(p.occasion, request, shown=key, draft=draft), p.name, design=design)
        transaction.set_rollback(True)
    return paper.respond(made, p.name)


@require('design_papers')
@xframe_options_sameorigin
@FRAMED
def letterhead_preview(request, key):
    """A Vorlage as a PDF, with the document its editor shows on it, for the made-up person: as posted, unsaved."""
    h, heads, found = head_of(key)
    p, design = shown_on(h, found, heads), h.page
    if request.method == 'POST':
        try:
            design = paper.clean(json.loads(request.POST.get('design', ''))['page'], letter=any(compose.letter(q.occasion) for q in on(h, found, heads)))
        except UNREAD as error:
            return framed_errors(request, error)
    return paper.respond(paper.pdf(sample(p.occasion, request, shown=p.key), h.name, design=design), h.name)


def framed_errors(request, error):
    return render(request, 'documents/frame.html', {'errors': getattr(error, 'messages', ['Das ließ sich nicht lesen.'])}, status=400)


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
    """A picture or font face gone, unless a Vorlage sets it."""
    art = get_object_or_404(Artwork, pk=pk)
    family = paper.OWN + slugify(art.name)
    for h in Letterhead.objects.all():
        style = h.page.get('style', {})
        if any(el.get('artwork') == art.pk for el in h.page.get('elements', [])) if art.kind == Artwork.Kind.PICTURE else (
            family in (style.get('font'), style.get('heading')) or any(el.get('font') == family for el in h.page.get('elements', []))
        ):
            return JsonResponse({'error': f'„{h.name}“ verwendet es noch.'}, status=400)
    art.delete()
    return JsonResponse({'ok': True})
