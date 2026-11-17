"""Every document ConCampus makes, as people name it. On paper, one for each occasion the code issues it on
(OCCASIONS): the Bescheide, letters each, and the Bescheinigungen, Zeugnisse and Urkunden. Each is the Hochschule's
own once it saves it (models.Paper), else ConCampus's Vorschlag (vorschlag); more of an occasion stand where the
Hochschule copies one, each for the Studiengänge it names (chosen). A document takes its page and its content from
the one it follows wherever it has none of its own (resolved). Besides, the messages and the texts on ConCampus's own
pages, worded each on its own (core.texts)."""
from copy import deepcopy
from dataclasses import dataclass

from django.utils.text import slugify

from core import paper
from core.texts import BAUSTEIN, BESCHEID, NACHRICHT, TEXTS

from .compose import vorschlag as content_of
from .models import Paper

KINDS = {  # URL segment: (the list's title, what it holds)
    'bescheide': ('Bescheide', 'Was die Hochschule entscheidet: ein Brief im Postfach, als PDF in der Akte. Erlassene behalten ihren Wortlaut.'),
    'bescheinigungen': ('Bescheinigungen und Urkunden', 'Gesetzt aus den Daten der Person, gestaltet und formuliert von der Hochschule.'),
    'nachrichten': ('Nachrichten', 'Was ins Postfach und per E-Mail geht und nichts entscheidet.'),
    'seiten': ('Auf Seiten', 'Was ConCampus auf seinen eigenen Seiten sagt.'),
}
OCCASIONS = {  # key: (kind, title): when ConCampus issues a document on paper
    'brief': ('bescheide', 'Brief'),  # any message, printed as a letter
    **{key: ('bescheide', spec.title) for key, spec in TEXTS.items() if spec.group == BESCHEID},
    **{key: ('bescheinigungen', title) for key, (title, _) in paper.DOCUMENTS.items() if key != 'brief'},
}
assert len(OCCASIONS) == 1 + sum(s.group == BESCHEID for s in TEXTS.values()) + len(paper.DOCUMENTS) - 1, 'one key for two occasions'
OWN_PAGE ={'brief', 'immatrikulationsbescheinigung', 'urkunde'}  # ConCampus's documents with a page of their own; the others take theirs


def vorschlag(occasion):
    """ConCampus's document for an occasion, unsaved: a Bescheid on the letter's page, a Bescheinigung on the
    Immatrikulationsbescheinigung's, each with content of its own."""
    kind = OCCASIONS[occasion][0]
    follows = '' if occasion in OWN_PAGE else 'brief' if kind == 'bescheide' else 'immatrikulationsbescheinigung'
    page = None if follows else deepcopy(paper.STANDARD[paper.DOCUMENTS[occasion][1]][1])
    return Paper(key=occasion, occasion=occasion, name=OCCASIONS[occasion][1], follows=follows, page=page, content=deepcopy(content_of(occasion)))


def papers():
    """{key: Paper}: for each occasion its document, the Hochschule's or ConCampus's, then its copies by name."""
    stored = {p.key: p for p in Paper.objects.prefetch_related('programs')}
    found = {}
    for occasion in OCCASIONS:
        found[occasion] = stored.pop(occasion, None) or vorschlag(occasion)
        found |= {p.key: p for p in sorted((p for p in stored.values() if p.occasion == occasion), key=lambda p: p.name)}
    return found


def resolved(key, part, found):
    """(a document's page or content, the document it is from): its own, else the one's it follows."""
    seen, at = set(), key
    while at in found and at not in seen:
        seen.add(at)
        if getattr(found[at], part) is not None:
            return getattr(found[at], part), found[at]
        at = found[at].follows
    fallback = vorschlag(found[key].occasion)  # what it follows has gone: as ConCampus has it
    own = getattr(fallback, part)
    return (own if own is not None else resolved(fallback.follows, part, found)[0]), found[key]


def followers(key, part, found):
    """The documents whose page or content is this one's, through others too, it aside."""
    return [p for p in found.values() if p.key != key and getattr(p, part) is None and resolved(p.key, part, found)[1].key == key]


def programs(p):
    return list(p.programs.all()) if p.pk else []


def chosen(occasion, program, found):
    """The document a case gets: a copy of the occasion's for its Studiengang, else the occasion's own."""
    pk = getattr(program, 'pk', None)
    return next((p for p in found.values() if p.occasion == occasion and p.key != occasion and pk and any(x.pk == pk for x in programs(p))), found[occasion])


def bescheid(occasion, values, after=(), program=None):
    """A Bescheid for its case, as it is issued (core.texts.send, students.Study.notify): (its document's key, its
    subject, its text for the Postfach, its content as set)."""
    from .compose import html, letter_values, subject, text

    found = papers()
    paper = chosen(occasion, program, found)
    content, values = resolved(paper.key, 'content', found)[0], letter_values(occasion, values, after)
    return paper.key, subject(content, values), text(content, values), html(content, values, {})


def page(key):
    """The page a document is set on (core.paper.pdf); one no longer there, the letter's."""
    found = papers()
    return resolved(key if key in found else 'brief', 'page', found)[0]


def free_key(name):
    taken = set(Paper.objects.values_list('key', flat=True)) | set(OCCASIONS) | {'neu'}
    base = slugify(name)[:50] or 'dokument'
    return next(k for n in range(1, 1000) if (k := base if n == 1 else f'{base}-{n}') not in taken)


@dataclass(frozen=True)
class Text:
    """A message or a page's text: worded on its own (core.texts)."""
    kind: str
    key: str
    title: str


def texts():
    """{(kind, key): Text}: the messages, then the texts on ConCampus's own pages."""
    found = [Text('nachrichten', key, spec.title) for key, spec in TEXTS.items() if spec.group == NACHRICHT]
    found += [Text('seiten', key, spec.title) for key, spec in TEXTS.items() if spec.group == BAUSTEIN and not spec.on]
    return {(t.kind, t.key): t for t in found}
