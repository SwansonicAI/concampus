"""The search box's memory, as Listary keeps file names: every word of the fields a search looks through, folded, with
the rows that hold it, so that a keystroke looks words up instead of reading whole tables. A row saved is up to date at
once; what changes through a relation (a person renamed, for their studies), or in another process, within a minute."""
import re
import time
from bisect import bisect_left
from collections import defaultdict
from itertools import islice, takewhile
from threading import Lock

from django.db.models.signals import post_delete, post_save

from .text import KIN, close, fold, wanted

FRESH = 60  # seconds an index is read again after
INDEXES, MAKING = {}, Lock()


class Index:
    def __init__(self, model, fields):
        self.model, self.fields, self.lock, self.built = model, fields, Lock(), None
        post_save.connect(self.saved, sender=model, weak=False)
        post_delete.connect(self.deleted, sender=model, weak=False)

    def read(self, rows):
        """{pk: its words}: each word of each field, and each field's beginning, for a word with a '-' ('BM-GM-02')."""
        found = defaultdict(set)
        for pk, *values in rows.values_list('pk', *self.fields):
            for text in (fold(str(v)) for v in values if v):
                found[pk] |= {*re.findall(r'[\w.]+', text), text[:60]}
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

    def look(self, word, starts):
        """The rows with a word that begins with `word`, or with `starts` off, holds it anywhere."""
        if starts:
            keys = takewhile(lambda k: k.startswith(word), islice(self.keys, bisect_left(self.keys, word), None))
        else:
            keys = (k for k in self.keys if word in k)
        return set().union(*(self.at[k] for k in keys))

    def alike(self, word, starts):
        """The rows of a word not found as typed: by a word meaning the same ('orchestra' → Orchester), else by one a
        letter off ('Katarina' → Katharina), also while still typing ('Katar' → Katharina)."""
        if found := set().union(*(self.look(form, starts) for form in KIN.get(word, ()) if form != word)):
            return found
        if len(word) < 5:
            return found
        near = (k for k in self.keys if k[0] == word[0] and any(close(word, k[:len(word) + d]) for d in (-1, 0, 1)))
        return set().union(*(self.at[k] for k in near))

    def find(self, words, starts):
        """The rows that hold every word."""
        with self.lock:
            if not self.built or time.monotonic() - self.built > FRESH:
                self.build()
            found = None
            for word in words:
                rows = self.look(word, starts) or self.alike(word, starts)
                found = rows if found is None else found & rows
            return found


def find(queryset, query, *fields, names='', starts=False):
    """text.matching(), from memory: the rows of `queryset` that hold every word sought in one of the fields."""
    if not (sought := wanted(query, names)):
        return queryset
    with MAKING:
        if (key := (queryset.model, fields)) not in INDEXES:
            INDEXES[key] = Index(*key)
    return queryset.filter(pk__in=INDEXES[key].find(sought, starts))
