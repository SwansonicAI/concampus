"""Forgiving text matching: letter case, accents and German umlaut spellings don't matter (Müller = Mueller = muller),
nor the ending of a word (Studienpläne finds Studienplan)."""
import re
import unicodedata
from functools import reduce
from operator import or_

from django.db.backends.signals import connection_created
from django.db.models import Func, Q, TextField
from django.dispatch import receiver


SPELLED = (('ae', 'a'), ('oe', 'o'), ('ue', 'u'))
ENDINGS = ('ern', 'en', 'er', 'es', 'e', 'n', 's')  # German plural and case endings, longest first


def fold(text):
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    for spelled, plain in SPELLED:
        text = text.replace(spelled, plain)
    return text


LETTERS = {c: fold(c) for c in map(chr, range(0xC0, 0x250))}  # Latin letters beyond ASCII, as names use them


def stem(word):
    """A folded word without its ending, so that every form of it matches: plane, planen, plans → plan."""
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[:-len(ending)]
    return word


def words(query):
    """The words of a search as they are compared: folded, without endings."""
    return [stem(fold(word)) for word in re.split(r'[\s,;]+', query) if word]


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


def matching(queryset, query, *fields, names='', starts=False):
    """Rows in which every word of `query` occurs, folded, in one of the fields; in any order, as fragments, or with
    `starts` only where a word of the field begins with it (names: 'Chen' is not in 'Bühnensprechen').

    Words that name what the rows are (`names`, e.g. 'raum') pick no row: 'Räume' lists them all, 'Raum Orgel' the organ rooms.
    """
    named = fold(names).split()
    wanted = [word for word in words(query) if not (len(word) >= 4 and any(name.startswith(word) for name in named))]
    if not wanted:
        return queryset
    found = queryset.model.objects.annotate(**{f'_f{i}': Fold(field) for i, field in enumerate(fields)})

    def where(i, word):
        if not starts:
            return Q(**{f'_f{i}__contains': word})
        return reduce(or_, [Q(**{f'_f{i}__startswith': word}), *(Q(**{f'_f{i}__contains': mark + word}) for mark in ' -„(/')])

    for word in wanted:
        found = found.filter(reduce(or_, (where(i, word) for i in range(len(fields)))))
    return queryset.filter(pk__in=found.values('pk'))
