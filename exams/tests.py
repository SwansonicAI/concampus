from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Count
from django.test import Client

from core.models import Message
from core.testing import Seeded, flashes
from courses.models import Course, Participation
from courses.panel import mean
from curriculum.models import Program, Rounding, rounded
from exams.views import EXAMS, attempts, exhausted, handed_in, offered
from students.models import Study


class ExamTest(Seeded):
    def test_register(self):
        exam = Course.objects.get(title__startswith='Musikgeschichte I · Mündliche', term__name='WiSe 2026/27')
        exam.opens = None  # registration under way, whatever day the tests run
        exam.save()
        study = Study.objects.get(person__username='student')
        r = self.login('student').post(f'/pruefungen/{exam.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertEqual(flashes(r), ['Angemeldet. Die Bestätigung liegt in Ihrem Postfach.'])
        self.assertTrue(exam.participations.filter(study=study).exists())

    def test_exams_of_a_hauptfach_are_for_its_students_only(self):
        exam = Course.objects.filter(kind='exam', term__name='WiSe 2026/27', subject__isnull=False).first()
        exam.opens = None
        exam.save()
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

    def test_examiners_give_steps_and_judge_before_seeing_the_others(self):
        resit = Course.objects.get(title__contains='Wiederholung')
        hallmann = resit.staff.get(last_name='Hallmann')
        examiner = Client()
        examiner.force_login(hallmann)
        rows = examiner.get(f'/pruefungen/{resit.pk}/').context['rows']
        self.assertTrue(all(p.blind for p in rows))  # the others have marked, Hallmann not yet
        first = rows[0].pk
        refused = examiner.post(f'/pruefungen/{resit.pk}/', {f'score-{first}-{hallmann.pk}': '1,5'}, follow=True)
        self.assertIn('keine Note', flashes(refused)[-1])
        examiner.post(f'/pruefungen/{resit.pk}/', {f'score-{first}-{hallmann.pk}': '1,7'})
        self.assertFalse(next(p for p in examiner.get(f'/pruefungen/{resit.pk}/').context['rows'] if p.pk == first).blind)

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


class OfficeTest(Seeded):
    """What the Prüfungsamt handles beyond the marks: withdrawals and no-shows, the last attempt, Anerkennungen, files of marks."""

    def test_a_withdrawal_needs_no_result_and_a_last_attempt_failed_ends_the_claim(self):
        resit = Course.objects.get(title__contains='Wiederholung')
        office = self.login('pruefungsamt')
        rows = office.get(f'/pruefungen/{resit.pk}/').context['rows']
        first, last = sorted(rows, key=lambda p: p.attempt)
        self.assertTrue(last.last)
        data = {f'score-{p.pk}-{e.pk}': str(v if v is not None else '') for p in rows for e, v in p.marks}
        data |= {f'grade-{p.pk}': '' for p in rows} | {f'remark-{first.pk}': 'rt', f'remark-{last.pk}': 'ne'}
        office.post(f'/pruefungen/{resit.pk}/', data)
        r = office.post(f'/pruefungen/{resit.pk}/freigabe/', follow=True)
        self.assertIn('1 Bescheid über endgültiges Nichtbestehen versandt.', ' '.join(flashes(r)))
        self.assertIn(last.module, exhausted(last.study))
        self.assertIn(f'{last.study.person}, Prüfung endgültig nicht bestanden', self.login('asa').get('/').content.decode())
        self.assertEqual(attempts(first.study.person, first.module), 1)  # the withdrawal is no attempt

    def test_the_office_enters_only_whom_the_exam_is_for_and_takes_a_mistake_back(self):
        exam = Course.objects.filter(kind='exam', released=None, subject=None, modules__program__degree='B.Mus.').first()
        other = Study.objects.filter(status='enrolled').exclude(program__modules__courses=exam).select_related('person').first()
        fits = Study.objects.filter(status='enrolled', program__modules__courses=exam).exclude(participations__course=exam).select_related('person').first()
        office = self.login('pruefungsamt')
        self.assertIn('nicht eingetragen', ' '.join(flashes(office.post(f'/lehre/{exam.pk}/hinzufuegen/', {'who': other.person.matriculation}, follow=True))))
        office.post(f'/lehre/{exam.pk}/hinzufuegen/', {'who': fits.person.matriculation})
        entered = exam.participations.get(study=fits)
        office.post(f'/lehre/{exam.pk}/austragen/', {'participation': entered.pk})
        self.assertFalse(exam.participations.filter(study__in=[other, fits]).exists())

    def test_a_whole_module_is_recognised_with_a_converted_grade(self):
        study = Study.objects.filter(status='enrolled', program__degree='B.Mus.').first()
        office = self.login('pruefungsamt')
        choices = office.get(f'/pruefungen/anerkennung/?studium={study.pk}').context['form'].fields['module'].choices
        options = next(options for _, options in choices[1:] if options[0][1] == 'Ganzes Modul')
        office.post(f'/pruefungen/anerkennung/?studium={study.pk}', {'module': options[0][0], 'foreign': '8', 'best': '10', 'worst': '5', 'note': 'mdw Wien, SoSe 2024'})
        recognised = Participation.objects.filter(study=study, course__kind='recognition')
        self.assertEqual((recognised.count(), set(recognised.values_list('grade', flat=True))), (len(options) - 1, {Decimal('2.2')}))

    def test_examiners_behind_with_their_marks_are_named_and_reminded_once_a_day(self):
        resit = Course.objects.get(title__contains='Wiederholung')
        office = self.login('pruefungsamt')
        self.assertIn('Wertung fehlt: Hallmann', office.get('/').text)
        office.post(f'/pruefungen/{resit.pk}/erinnern/')
        office.post(f'/pruefungen/{resit.pk}/erinnern/')
        self.assertEqual(Message.objects.filter(person__last_name='Hallmann', subject=f'Ihre Wertung fehlt: {resit.title}').count(), 1)

    def test_marks_come_from_a_file(self):
        exam = Course.objects.annotate(n=Count('staff', distinct=True)).filter(kind='exam', released=None, n=1).first()
        studies = Study.objects.filter(status='enrolled', program__modules__courses=exam).exclude(participations__course=exam).distinct()[:3]
        Participation.objects.bulk_create(Participation(course=exam, study=s, module=exam.modules.filter(program=s.program).first()) for s in studies)
        lines = ['Matrikelnummer;Note', *(f'{p.study.person.matriculation};1,7' for p in exam.participations.select_related('study__person')), '999999;2,0']
        r = self.login(exam.staff.get().username).post(f'/pruefungen/{exam.pk}/noten/', {'file': SimpleUploadedFile('noten.csv', '\n'.join(lines).encode())}, follow=True)
        self.assertIn('Nicht angemeldet: 999999.', flashes(r)[0])
        self.assertEqual(set(exam.participations.values_list('grade', 'passed')), {(Decimal('1.7'), True)})


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
        self.assertEqual({r.module_id: r.grade for r in study.record()}[module.pk], Decimal('1.7'))  # the mean of 1.3 and 2.0, rounded half up

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
        exam.opens = None
        exam.save()
        r = self.login('student').post(f'/pruefungen/{exam.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertIn('Alle 0 Versuche', flashes(r)[0])

    def test_rounding(self):
        program = Program(rounding=Rounding.CUT)
        self.assertEqual(rounded(Decimal('1.66'), program.rounding), Decimal('1.6'))
        self.assertEqual(rounded(Decimal('1.66'), Rounding.HALF_UP), Decimal('1.7'))
        self.assertEqual(mean([Decimal('16'), Decimal('16.15')], Rounding.CUT), Decimal('16.0'))
