"""Forgiving text matching: letter case, accents and German umlaut spellings don't matter (Müller = Mueller = muller),
nor the ending of a word (Studienpläne finds Studienplan)."""
import re
import unicodedata
from functools import lru_cache, reduce
from operator import or_

from django.db.backends.signals import connection_created
from django.db.models import Func, Q, TextField
from django.dispatch import receiver

from .synonyms import ACTIONS, KINDS, MUSIC, STOP, SYNONYMS


SPELLED = (('ae', 'a'), ('oe', 'o'), ('ue', 'u'))
ENDINGS = ('ern', 'en', 'er', 'es', 'e', 'n', 's')  # German plural and case endings, longest first


def handle(first, last):
    """Mail-safe name: 'Nicolás', 'Ferreira' → 'nicolas.ferreira'."""
    name = f'{first}.{last}'.lower().replace(' ', '-')
    for a, b in [('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss'), ('ł', 'l'), ('’', '')]:
        name = name.replace(a, b)
    return unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()


@lru_cache(maxsize=1 << 16)  # SQLite calls it for every row, the same names over and over
def fold(text):
    text = (text or '').casefold()
    if not text.isascii():
        text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    for spelled, plain in SPELLED:
        text = text.replace(spelled, plain)
    return text


LETTERS = {c: fold(c) for c in map(chr, range(0xC0, 0x250))}  # Latin letters beyond ASCII, as names use them


def stem(word):
    """A folded word without its ending, so that every form of it matches: plane, planen, plans → plan. A double s is
    the word's own: brass, Kontrabass."""
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4 and not (ending == 's' and word.endswith('ss')):
            return word[:-len(ending)]
    return word


def words(query):
    """The words of a search as they are compared: folded, without endings."""
    return [stem(fold(word)) for word in re.split(r'[\s,;]+', query) if word]


KIN = {}  # each word, as compared, with all words meaning the same
for line in ACTIONS + SYNONYMS + MUSIC:
    for word in (meaning := set(words(line))):
        KIN[word] = KIN.get(word, set()) | meaning
ACTS, EXACT = set(words(' '.join(ACTIONS))), set(words(' '.join(MUSIC)))
SORTS = set().union(*(KIN[word] for word in words(KINDS)))
STOPPED = set(words(' '.join(STOP)))


def terms(query):
    """The words of a search, as words() has them, two the table knows as one taken as one: 'ear training', 'double
    bass' (SYNONYMS: 'ear-training', 'double-bass')."""
    raw, found = [fold(word) for word in re.split(r'[\s,;]+', query) if word], []
    while raw:
        pair = stem('-'.join(raw[:2])) if len(raw) > 1 else ''
        found.append(pair if pair in KIN else stem(raw[0]))
        raw = raw[2:] if pair in KIN else raw[1:]
    return found


def close(a, b):
    """At most one letter apart: one wrong, missing, extra, or two swapped ('cousre'); the first right, as typing
    keeps it ('Kroll' is no 'Rollen')."""
    if abs(len(a) - len(b)) > 1 or a[:1] != b[:1]:
        return False
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return a[i + 1:] == b[i + 1:] or a[i + 1:] == b[i:] or a[i:] == b[i + 1:] or a[i:i + 2] == b[i:i + 2][::-1] and a[i + 2:] == b[i + 2:]


def alike(word, typing=False):
    """The word and those meaning the same; a word misspelt by a letter, as the word meant ('craete'); one still being
    typed, as every word it may become ('cour' → course, Kurs …)."""
    if kin := KIN.get(word) or KIN.get(stem(word)):  # 'letters' → 'letter' → 'lett', as the table has it
        return kin | {word}
    found = {word}
    for key, kin in KIN.items():
        if typing and len(word) >= 4 and key.startswith(word) or len(word) >= 5 and close(word, key):
            found |= kin
    return found


def meant(query):
    """The words of a search for a page, each with all it may stand for (alike), without those that carry no meaning."""
    sought = [w for w in terms(query) if w not in STOPPED] or terms(query)
    return [(w, alike(w, typing=n == len(sought) - 1)) for n, w in enumerate(sought)]


def near(word, forms, token):
    """How near a word of a page, folded, comes to one sought: 3 the word or one meaning the same, 2 its beginning, 1 a
    part of a compound either way ('Freigabe' in 'Notenfreigabe' typed) or one letter off. Beyond the word as typed,
    only a compound's part counts: 'Raum' in 'Raumplan', not 'term' in 'Termin', nor 'krank' in 'Krankenkasse', a word
    of a meaning of its own."""
    stemmed = stem(token)
    if stemmed in forms or token in forms:
        return 3
    parts = [f for f in forms if len(stemmed) - len(f) >= 3 and (f == word or f in KIN.get(stemmed, {f}))]
    if token.startswith(word) or any(token.startswith(f) for f in parts):
        return 2
    return int(any(len(f) >= 4 and f in token for f in parts) or len(stemmed) >= 5 and stemmed in word or len(word) >= 5 and close(word, stemmed))


def fit(sought, title, more=''):
    """How well a page fits a search (meant): (share of the words found, score), a word in the title counting twice.
    Words of doing (ACTIONS) add to the score only when a thing is sought too: they say what to do with it."""
    heads, rest = re.findall(r'\w+', fold(title)), re.findall(r'\w+', fold(more))
    counted = [forms for _, forms in sought if not forms <= ACTS] or [forms for _, forms in sought]
    found = score = 0
    for word, forms in sought:
        best = max([2 * near(word, forms, t) for t in heads] + [near(word, forms, t) for t in rest] + [0])
        found, score = found + bool(best and forms in counted), score + best
    return found / max(len(counted), 1), score


@receiver(connection_created)
def teach_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        connection.connection.create_function('fold', 1, fold, deterministic=True)


class Fold(Func):
    function = 'fold'
    output_field = TextField()

    def as_postgresql(self, compiler, connection):
        """Postgres has no `fold`: the same steps in SQL, its table of letters taken from `fold` itself."""
        sql, params = compiler.compile(self.source_expressions[0])
        for letter, plain in LETTERS.items():
            if len(plain) > 1:  # ß → ss
                sql, params = f'replace({sql}, %s, %s)', [*params, letter, plain]
        single = {letter: plain for letter, plain in LETTERS.items() if len(plain) == 1 and plain != letter}
        sql, params = f'lower(translate({sql}, %s, %s))', [*params, ''.join(single), ''.join(single.values())]
        for spelled, plain in SPELLED:
            sql, params = f'replace({sql}, %s, %s)', [*params, spelled, plain]
        return sql, params


def untimed(note):
    """A programme without the times its works start at in a video ('6:40 N. Paganini: …'), as played in person."""
    return re.sub(r'^\d{1,2}:[0-5]\d\s+', '', note, flags=re.M)


def wanted(query, names=''):
    """The words of a search for rows, as compared. Words that name what the rows are (`names`, e.g. 'raum'), or mean
    the same, pick no row: 'Räume' and 'rooms' list them all, 'Raum Orgel' the organ rooms."""
    named = fold(names).split()
    return [word for word in terms(query) if not (len(word) >= 4 and any(name.startswith(form) for name in named for form in KIN.get(word, {word})))]


def matching(queryset, query, *fields, names='', starts=False):
    """Rows in which every word of `query` occurs, folded, in one of the fields; in any order, as fragments, or with
    `starts` only where a word of the field begins with it (names: 'Chen' is not in 'Bühnensprechen'); see wanted()."""
    if not (sought := wanted(query, names)):
        return queryset
    found = queryset.model.objects.annotate(**{f'_f{i}': Fold(field) for i, field in enumerate(fields)})

    def where(i, word):
        if not starts:
            return Q(**{f'_f{i}__contains': word})
        return reduce(or_, [Q(**{f'_f{i}__startswith': word}), *(Q(**{f'_f{i}__contains': mark + word}) for mark in ' -„(/')])

    for word in sought:
        found = found.filter(reduce(or_, (where(i, word) for i in range(len(fields)))))
    return queryset.filter(pk__in=found.values('pk'))
