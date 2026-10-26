from django.db import models
from django.utils import timezone

from core.models import Institute, Person, Tracked


class Instrument(Tracked, models.Model):
    """An instrument the Hochschule lends, to students for their studies and to its teachers."""

    capabilities = ('manage_instruments',)
    scope = (['institute'], [])

    number = models.CharField('Inventarnummer', max_length=30, unique=True)
    name = models.CharField('Instrument', max_length=80, help_text='Z. B. „Violoncello 4/4“ oder „Cajón“.')
    maker = models.CharField('Hersteller und Baujahr', max_length=120, blank=True)
    institute = models.ForeignKey(Institute, models.SET_NULL, null=True, blank=True, related_name='instruments', verbose_name='Institut')
    note = models.TextField('Zustand und Zubehör', blank=True)
    available = models.BooleanField('Verleihbar', default=True, help_text='Ohne Haken, etwa während einer Reparatur, wird es nicht verliehen.')

    class Meta:
        ordering = ['name', 'number']
        verbose_name = 'Leihinstrument'
        verbose_name_plural = 'Leihinstrumente'

    def __str__(self):
        return f'{self.name} ({self.number})'


class Loan(Tracked, models.Model):
    """An instrument lent to a person until a day. Not back in time, it holds up their Rückmeldung (Person.missing)."""

    owner = 'person_id'
    capabilities = ('manage_instruments',)
    scope = (['instrument__institute'], [])

    instrument = models.ForeignKey(Instrument, models.PROTECT, related_name='loans', verbose_name='Instrument')
    person = models.ForeignKey(Person, models.CASCADE, related_name='loans', verbose_name='Ausgeliehen von')
    start = models.DateField('Ausgeliehen am', default=timezone.localdate)
    due = models.DateField('Rückgabe bis')
    returned = models.DateField('Zurückgegeben am', null=True, blank=True)
    note = models.CharField('Notiz', max_length=200, blank=True)
    condition = models.CharField('Zustand bei Ausgabe', max_length=200, blank=True)
    returned_condition = models.CharField('Zustand bei Rückgabe', max_length=200, blank=True)
    reminded = models.DateField('Erinnert am', null=True, blank=True)

    class Meta:
        ordering = ['-start']
        verbose_name = 'Leihe'
        verbose_name_plural = 'Leihen'
        constraints = [models.UniqueConstraint(fields=['instrument'], condition=models.Q(returned=None), name='one_open_loan')]

    def __str__(self):
        return f'{self.instrument} · {self.person}'

    @property
    def overdue(self):
        return self.returned is None and self.due < timezone.localdate()
