from django.db import models

from core.models import Tracked


class Content(Tracked, models.Model):
    """A document's content as the Hochschule composes it (documents.compose): the office it comes from and its blocks in
    order; without one, ConCampus's Vorschlag."""

    capabilities = ('word_texts',)

    key = models.SlugField('Dokument', max_length=40, unique=True)
    content = models.JSONField('Inhalt', default=dict)

    class Meta:
        ordering = ['key']
        verbose_name = 'Inhalt eines Dokuments'
        verbose_name_plural = 'Inhalte der Dokumente'

    def __str__(self):
        from core.paper import DOCUMENTS

        return DOCUMENTS.get(self.key, (self.key,))[0]
