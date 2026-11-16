"""Every document ConCampus makes, as people name it, each with what can be worded on it and the Briefbogen it is set on:
the Bescheide, letters of one text each and the texts every Bescheid carries; the Bescheinigungen and Urkunden, set
from a person's records around the few texts the Hochschule words; the messages; and the texts on ConCampus's own
pages. Which there are is the code's: each is made where something happens (core.texts, core.paper)."""
from dataclasses import dataclass

from core import paper
from core.texts import BAUSTEIN, BESCHEID, NACHRICHT, TEXTS

KINDS = {  # URL segment: (the list's title, what it holds)
    'bescheide': ('Bescheide', 'Was die Hochschule entscheidet: ein Brief im Postfach, als PDF in der Akte. Gesendete behalten ihren Wortlaut.'),
    'bescheinigungen': ('Bescheinigungen und Urkunden', 'Gesetzt aus den Daten der Person; die Hochschule formuliert, was um sie herum steht.'),
    'nachrichten': ('Nachrichten', 'Was ins Postfach und per E-Mail geht und nichts entscheidet.'),
    'seiten': ('Auf Seiten', 'Was ConCampus auf seinen eigenen Seiten sagt.'),
}
WITHIN = {'ablehnung': {'begruendung': 'ablehnung_begruendung'}}  # a Bescheid's placeholder that a text of its own fills
FOLLOWING = {'exmatrikulation': ('erstattung', 'auslaenderbehoerde')}  # what follows a Bescheid's text, where it applies


@dataclass(frozen=True)
class Document:
    kind: str
    key: str
    title: str
    texts: tuple  # what is worded on it, its own first
    paper: str = ''  # its Briefbogen's slot in Institution.papers; none: it is not printed

    @property
    def letter(self):
        return self.kind == 'bescheide'


def documents():
    """{(kind, key): Document}, in the order of KINDS."""
    found = []
    for key, spec in TEXTS.items():
        if spec.group == BESCHEID:
            texts = (key, *WITHIN.get(key, {}).values(), *FOLLOWING.get(key, ()), *('rechtsbehelf',) * spec.appealable, 'absender_bescheide')
            found.append(Document('bescheide', key, spec.title, texts, paper.OWN_LETTER + key))
    for doc, (title, _) in paper.DOCUMENTS.items():
        if doc not in paper.LETTERS:
            found.append(Document('bescheinigungen', doc, title, tuple(key for key, spec in TEXTS.items() if doc in spec.on), doc))
    found += [Document('nachrichten', key, spec.title, (key,)) for key, spec in TEXTS.items() if spec.group == NACHRICHT]
    found += [Document('seiten', key, spec.title, (key,)) for key, spec in TEXTS.items() if spec.group == BAUSTEIN and not spec.on]
    return {(d.kind, d.key): d for d in found}


def shared(found=None):
    """{text: where else it stands, in words}, for a text on more than one document: „in 23 Bescheiden“."""
    on = {}
    for d in (found or documents()).values():
        for key in d.texts:
            on.setdefault(key, []).append(d)
    return {key: f'in {len(ds)} {"Bescheiden" if all(d.letter for d in ds) else "Dokumenten"}' for key, ds in on.items() if len(ds) > 1}
