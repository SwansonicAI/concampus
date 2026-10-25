from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from core.testing import Seeded, flashes
from courses.models import Course, Participation
from courses.panel import mean
from curriculum.models import Program, Rounding, rounded
from exams.views import EXAMS, handed_in, offered
from students.models import Study


class ExamTest(Seeded):
    def test_register(self):
        exam = Course.objects.get(title__startswith='Musikgeschichte I · Mündliche', term__name='WiSe 2026/27')
        study = Study.objects.get(person__username='student')
        r = self.login('student').post(f'/pruefungen/{exam.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertEqual(flashes(r), ['Angemeldet. Die Bestätigung liegt in Ihrem Postfach.'])
        self.assertTrue(exam.participations.filter(study=study).exists())

    def test_exams_of_a_hauptfach_are_for_its_students_only(self):
        exam = Course.objects.filter(kind='exam', term__name='WiSe 2026/27', subject__isnull=False, participations__isnull=False).first()
        module = exam.modules.first()
        others = Study.objects.filter(status='enrolled', program=module.program).exclude(subject=exam.subject).exclude(participations__course=exam)
        study = next(s for s in others if s.semester >= module.semester)
        self.assertNotIn(exam, offered(study.person))
        client = Client()
        client.force_login(study.person)
        r = client.post(f'/pruefungen/{exam.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertEqual(flashes(r), [f'Diese Prüfung ist für das Hauptfach {exam.subject}.'])

    def test_the_list_finds_exams_by_candidate(self):
        p = Course.objects.filter(kind=Course.Kind.EXAM, term__name='WiSe 2026/27', participations__isnull=False).first().participations.select_related('study__person').first()
        found = self.login('pruefungsamt').get('/pruefungen/', {'q': p.study.person.matriculation}).context['exams']
        self.assertEqual({c.pk for c in found}, set(p.study.participations.filter(course__kind__in=EXAMS, course__term__name='WiSe 2026/27').values_list('course', flat=True)))

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


class ThesisTest(Seeded):
    def test_registered_handed_in_marked_and_credited(self):
        study = Study.objects.filter(status='enrolled', program__degree='B.Ed.').exclude(participations__course__kind='thesis').first()
        office = self.login('pruefungsamt')
        form = office.get(f'/pruefungen/abschlussarbeit/{study.pk}/').context['form']
        module, examiners = form.fields['module'].initial, list(form.fields['staff'].queryset[:2])
        self.assertEqual(module.title[:14], 'Bachelorarbeit')
        office.post(f'/pruefungen/abschlussarbeit/{study.pk}/', {
            'module': module.pk, 'title': 'Klassenmusizieren', 'staff': [e.pk for e in examiners], 'deadline': form.initial['deadline'].strftime('%d.%m.%Y'),
        })
        p = Participation.objects.get(study=study, course__kind='thesis')
        student = Client()
        student.force_login(study.person)
        student.post(f'/pruefungen/teilnahme/{p.pk}/abgabe/', {'file': SimpleUploadedFile('arbeit.pdf', b'%PDF-1.4', content_type='application/pdf')})
        self.assertEqual(handed_in(p).title, 'arbeit.pdf')
        for examiner, grade in zip(examiners, ['1,3', '2,0']):
            self.login(examiner.username).post(f'/pruefungen/{p.course_id}/', {f'score-{p.pk}-{examiner.pk}': grade})
        office.post(f'/pruefungen/{p.course_id}/freigabe/')
        self.assertEqual({r.module_id: r.grade for r in study.record()}[module.pk], Decimal('1.6'))  # the mean of 1.3 and 2.0, cut

    def test_diploma_supplement(self):
        study = Study.objects.get(person__username='student')
        self.assertContains(self.login('pruefungsamt').get(f'/pruefungen/leistungen/{study.pk}/diploma-supplement/'), 'Bachelor of Music (B.Mus.)')


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
