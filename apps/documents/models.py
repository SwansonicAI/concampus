from django.db import models

from core.models import Tracked


class Letterhead(Tracked, models.Model):
    """A Vorlage: the page a document is set on, its head and foot, its text areas and type (core.paper), as the
    Hochschule keeps it. Without one under its key, ConCampus's own (core.paper.STANDARD) stands."""

    capabilities = ('design_papers',)

    key = models.SlugField('Schlüssel', max_length=60, unique=True)
    name = models.CharField('Name', max_length=120)
    page = models.JSONField('Seite')

    class Meta:
        ordering = ['name']
        verbose_name = 'Vorlage'
        verbose_name_plural = 'Vorlagen'

    def __str__(self):
        return self.name


class Paper(Tracked, models.Model):
    """A document ConCampus issues, as the Hochschule has made it its own (catalogue): what it says, on the Vorlage
    it is set on. Without one under an occasion's key, ConCampus's Vorschlag stands."""

    capabilities = ('word_texts', 'design_papers')

    key = models.SlugField('Schlüssel', max_length=60, unique=True)
    occasion = models.CharField('Anlass', max_length=40, help_text='Wann ConCampus es ausstellt (documents.catalogue.OCCASIONS).')
    name = models.CharField('Name', max_length=120)
    programs = models.ManyToManyField('curriculum.Program', blank=True, related_name='papers', verbose_name='Gilt für',
                                      help_text='Wofür es statt des Dokuments seines Anlasses gilt; leer: für niemanden eigens.')
    letterhead = models.SlugField('Vorlage', max_length=60, blank=True, help_text='Die Vorlage, auf der es steht; leer: die seines Anlasses.')
    content = models.JSONField('Inhalt', null=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Dokument'
        verbose_name_plural = 'Dokumente'

    def __str__(self):
        return self.name
