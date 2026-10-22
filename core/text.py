"""Forgiving text matching: letter case, accents and German umlaut spellings don't matter (Müller = Mueller = muller)."""
import re
import unicodedata
from functools import reduce
from operator import or_

from django.db.backends.signals import connection_created
from django.db.models import Func, Q, TextField
from django.dispatch import receiver


def fold(text):
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    for spelled, plain in (('ae', 'a'), ('oe', 'o'), ('ue', 'u')):
        text = text.replace(spelled, plain)
    return text


@receiver(connection_created)
def teach_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        connection.connection.create_function('fold', 1, fold, deterministic=True)


class Fold(Func):
    function = 'fold'
    output_field = TextField()


def matching(queryset, query, *fields):
    """Rows in which every word of `query` occurs, folded, in one of the fields; in any order, as fragments."""
    words = [fold(word) for word in re.split(r'[\s,;]+', query) if word]
    if not words:
        return queryset
    found = queryset.model.objects.annotate(**{f'_f{i}': Fold(field) for i, field in enumerate(fields)})
    for word in words:
        found = found.filter(reduce(or_, (Q(**{f'_f{i}__contains': word}) for i in range(len(fields)))))
    return queryset.filter(pk__in=found.values('pk'))
