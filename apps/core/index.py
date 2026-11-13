"""The search box's memory, as Listary keeps file names: every word of the fields a search looks through, folded, with
the rows that hold it, so that a keystroke looks words up instead of reading whole tables. A row saved is up to date at
once; what changes through a relation (a person renamed, for their studies), or in another process, within a minute."""
import re
import time
from bisect import bisect_left
from collections import defaultdict
from itertools import islice, takewhile
from threading import Lock

from django.db.models import Case, Value, When
from django.db.models.signals import post_delete, post_save

from .text import ACTS, EXACT, KIN, close, fold, wanted

FRESH = 60  # seconds an index is read again after
HEAD = '^'  # before a field's beginning, kept as a word of its own: 'Klavier (Pflichtfach)' begins with 'klavier'
MEANT = '~'  # before a word of a field that is no name, kept for words meaning the same: 'Saal' for 'room', not Saalfeld
NAMES = ('first_name', 'last_name', 'birth_name')
INDEXES, MAKING = {}, Lock()


class Index:
    def __init__(self, model, fields):
        self.model, self.fields, self.lock, self.built = model, fields, Lock(), None
        post_save.connect(self.saved, sender=model, weak=False)
        post_delete.connect(self.deleted, sender=model, weak=False)

    def read(self, rows):
        """{pk: its words}: each word of each field, each field's beginning (HEAD), and each word of a field not a name
        once more (MEANT)."""
        found = defaultdict(set)
        for pk, *values in rows.values_list('pk', *self.fields):
            for field, value in zip(self.fields, values):
                if value:
                    words = re.findall(r'[\w.]+', text := fold(str(value)))
                    found[pk] |= {*words, HEAD + text[:60], *(() if field.endswith(NAMES) else (*(MEANT + w for w in words), MEANT + HEAD + text[:60]))}
        return found

    def build(self):
        self.of, self.at = self.read(self.model._default_manager.all()), defaultdict(set)
        for pk, words in self.of.items():
            for word in words:
                self.at[word].add(pk)
        self.keys, self.built = sorted(self.at), time.monotonic()

    def drop(self, pk):
        for word in self.of.pop(pk, ()):
            self.at[word].discard(pk)

    def saved(self, instance, **kwargs):
        with self.lock:
            if self.built:
                self.drop(instance.pk)
                for pk, words in self.read(self.model._default_manager.filter(pk=instance.pk)).items():
                    self.of[pk] = words
                    for word in words:
                        if word not in self.at:
                            self.keys.insert(bisect_left(self.keys, word), word)
                        self.at[word].add(pk)

    def deleted(self, instance, **kwargs):
        with self.lock:
            if self.built:
                self.drop(instance.pk)

    def begun(self, prefix):
        """The rows with a word beginning with `prefix`."""
        keys = takewhile(lambda k: k.startswith(prefix), islice(self.keys, bisect_left(self.keys, prefix), None))
        return set().union(*(self.at[k] for k in keys))

    def look(self, word, starts):
        """The rows with a word or field that begins with `word`, or with `starts` off, holds it anywhere."""
        if starts:
            return self.begun(word) | self.begun(HEAD + word)
        return set().union(*(self.at[k] for k in self.keys if word in k))

    def alike(self, word, starts):
        """The rows of a word as meant rather than typed: of a word known, by those meaning the same ('orchestra' →
        Orchester); of one unknown, by one a letter off ('Katarina' → Katharina), also while still typing ('Katar')."""
        if word in KIN:
            return set().union(*(self.begun(MEANT + form) if starts else self.look(MEANT + form, False) for form in KIN[word] if form != word))
        found = set()
        if len(word) >= 5:
            near = (k for k in self.keys if k[0] == word[0] and any(close(word, k[:len(word) + d]) for d in (-1, 0, 1)))
            found |= set().union(*(self.at[k] for k in near))
        return found

    def find(self, words, starts, wide=False):
        """The rows that hold every word: a word of music always also by those meaning the same ('piano' → Klavier),
        any other only `wide`, as meant (alike); and those of them a field of which begins with a word, or a field not
        a name with one meaning the same, and those that hold one whole, to come first."""
        with self.lock:
            if not self.built or time.monotonic() - self.built > FRESH:
                self.build()
            found = None
            for word in words:
                rows = self.look(word, starts) | (self.alike(word, starts) if wide or word in EXACT else set())
                found = rows if found is None else found & rows
            heads = [HEAD + w for w in words] + [MEANT + HEAD + f for w in words for f in KIN.get(w, ())]
            first = set().union(*(self.begun(h) for h in heads)) & found
            return found, first, set().union(*(self.at.get(w, ()) for w in words)) & found


def find(queryset, query, *fields, names='', starts=False):
    """text.matching(), from memory: the rows of `queryset` that hold every word sought in one of the fields, best
    first (`_fit`), then in the queryset's order. A word a letter off only where nothing the person may see holds the
    words as typed; words of doing ('create', 'book') find nothing in records, which have no verbs."""
    if not (sought := wanted(query, names)):
        return queryset.annotate(_fit=Value(0))
    sought = [w for w in sought if not (w in KIN and KIN[w] <= ACTS)] or sought
    with MAKING:
        if (key := (queryset.model, fields)) not in INDEXES:
            INDEXES[key] = Index(*key)
    found, first, whole = INDEXES[key].find(sought, starts)
    if not queryset.filter(pk__in=found).exists():
        found, first, whole = INDEXES[key].find(sought, starts, wide=True)
    fit = Case(When(pk__in=first, then=0), When(pk__in=whole, then=1), default=2)
    return queryset.filter(pk__in=found).annotate(_fit=fit).order_by('_fit', *(queryset.query.order_by or queryset.model._meta.ordering))
