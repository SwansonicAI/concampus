from django.db import models

from core.models import Tracked


class Paper(Tracked, models.Model):
    """A document ConCampus issues, as the Hochschule has made it its own (catalogue): its page and its content, each its
    own or, empty, the document's it follows. Without one under an occasion's key, ConCampus's Vorschlag stands."""

    capabilities = ('word_texts', 'design_papers')

    key = models.SlugField('Schlüssel', max_length=60, unique=True)
    occasion = models.CharField('Anlass', max_length=40, help_text='Wann ConCampus es ausstellt (documents.catalogue.OCCASIONS).')
    name = models.CharField('Name', max_length=120)
    programs = models.ManyToManyField('curriculum.Program', blank=True, related_name='papers', verbose_name='Gilt für',
                                      help_text='Wofür es statt des Dokuments seines Anlasses gilt; leer: für niemanden eigens.')
    follows = models.SlugField('Wie', max_length=60, blank=True, help_text='Das Dokument, dessen Seite und Inhalt es übernimmt, wo es keine eigenen hat.')
    page = models.JSONField('Seite', null=True, blank=True)
    content = models.JSONField('Inhalt', null=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Dokument'
        verbose_name_plural = 'Dokumente'

    def __str__(self):
        return self.name
