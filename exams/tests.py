from decimal import Decimal

from core.testing import Seeded, flashes
from courses.models import Course
from courses.panel import mean
from curriculum.models import Program, Rounding, rounded
from students.models import Study


class ExamTest(Seeded):
    def test_register(self):
        exam = Course.objects.get(title__startswith='Musikgeschichte I · Mündliche', term__name='WiSe 2026/27')
        study = Study.objects.get(person__username='student')
        r = self.login('student').post(f'/pruefungen/{exam.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertEqual(flashes(r), ['Angemeldet. Die Bestätigung liegt in Ihrem Postfach.'])
        self.assertTrue(exam.participations.filter(study=study).exists())

    def test_results_follow_the_panel_then_lock_on_release(self):
        resit = Course.objects.get(title__contains='Wiederholung')
        office = self.login('pruefungsamt')
        data = {}
        for p in office.get(f'/pruefungen/{resit.pk}/').context['rows']:
            data |= {f'score-{p.pk}-{e.pk}': str(value if value is not None else '2,0') for e, value in p.marks}
            data[f'grade-{p.pk}'] = ''
        office.post(f'/pruefungen/{resit.pk}/', data)
        self.assertTrue(all(passed for _, passed in resit.participations.values_list('grade', 'passed')))
        office.post(f'/pruefungen/{resit.pk}/freigabe/')
        self.assertIsNotNone(Course.objects.get(pk=resit.pk).released)
        self.assertEqual(office.post(f'/pruefungen/{resit.pk}/', data).status_code, 403)
        study = resit.participations.first().study
        self.assertTrue(any(p.module.code.startswith('BM-BS-01') for p in study.record()))


class RegulationTest(Seeded):
    """Attempts, rounding and honours are the Prüfungsordnung's, set per programme."""

    def test_attempts(self):
        exam = Course.objects.get(title__startswith='Musikgeschichte I · Mündliche', term__name='WiSe 2026/27')
        study = Study.objects.get(person__username='student')
        study.program.attempts = 0
        study.program.save()
        r = self.login('student').post(f'/pruefungen/{exam.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertIn('alle 0 Versuche', flashes(r)[0])

    def test_rounding(self):
        program = Program(rounding=Rounding.CUT)
        self.assertEqual(rounded(Decimal('1.66'), program.rounding), Decimal('1.6'))
        self.assertEqual(rounded(Decimal('1.66'), Rounding.HALF_UP), Decimal('1.7'))
        self.assertEqual(mean([Decimal('16'), Decimal('16.15')], Rounding.CUT), Decimal('16.0'))
