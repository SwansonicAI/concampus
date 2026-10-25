from datetime import timedelta

from django.utils import timezone

from core.models import Person
from core.testing import Seeded
from .models import Instrument, Loan


class LoanTest(Seeded):
    def test_an_overdue_instrument_holds_up_the_rueckmeldung(self):
        office, student = self.login('veranstaltungsbuero'), Person.objects.get(username='student')
        free = Instrument.objects.filter(available=True).exclude(pk__in=Loan.objects.filter(returned=None).values('instrument')).first()
        office.post(f'/instrumente/{free.pk}/verleihen/', {'who': student.matriculation, 'due': f'{timezone.localdate() - timedelta(days=1):%d.%m.%Y}'})
        loan = Loan.objects.get(instrument=free, returned=None)
        self.assertIn(f'Leihinstrument zurückgeben: {free.name}', student.missing())
        office.post(f'/instrumente/leihe/{loan.pk}/rueckgabe/')
        self.assertNotIn(f'Leihinstrument zurückgeben: {free.name}', Person.objects.get(pk=student.pk).missing())
