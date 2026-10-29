from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from core.models import Message, Person, Term
from core.testing import Seeded, flashes
from courses.models import Course, Participation, Score
from students.models import Document, DocumentType, Study, StudyTerm
from .retention import forget
from .views import checklist, incomplete, recommendations, vacancies

PANEL = {'X-Panel': '1'}


def upload(name, kind='video'):
    return {'kind': kind, 'file': SimpleUploadedFile(name, b'\x00\x00\x00\x18ftypmp42', content_type='video/mp4')}


class DecisionTest(Seeded):
    def test_decide_accept_enrol(self):
        asa = self.login('asa')
        study = Study.objects.filter(status='applied', participations=None).exclude(person__username='bewerbung').first()  # no stages to wait for
        asa.post(f'/aufnahme/bewerbung/{study.pk}/entscheidung/', {'status': 'admitted'})
        self.assertTrue(Message.objects.filter(person=study.person, subject='Bescheid über die Eignungsprüfung: Studienplatzangebot').exists())
        applicant = Client()
        applicant.force_login(study.person)
        self.assertTrue(flashes(applicant.post(f'/aufnahme/meine/{study.pk}/annahme/', {'decision': 'yes'}, follow=True)))
        applicant.post(f'/aufnahme/meine/{study.pk}/annahme/', {'decision': 'yes', 'insurer': 'BARMER', 'insurance': 'gesetzlich', 'home': '16055', 'external_terms': 0})
        study.person.refresh_from_db()
        self.assertEqual(study.person.insurer, 'BARMER')
        self.assertTrue(StudyTerm.objects.filter(study=study).exists())
        asa.post(f'/studierende/{study.pk}/versicherung/')
        asa.post(f'/studierende/{study.pk}/immatrikulieren/')
        self.assertEqual(Study.objects.get(pk=study.pk).status, Study.Status.ADMITTED)  # not before the fee is in
        asa.post(f'/studierende/{study.pk}/beitrag/')
        asa.post(f'/studierende/{study.pk}/immatrikulieren/')
        study.refresh_from_db()
        self.assertEqual(study.status, Study.Status.ENROLLED)


    def test_results_decide_many_at_once_down_the_ranking(self):
        term, asa = Term.objects.get(name='SoSe 2027'), Person.objects.get(username='asa')
        intake = Course.objects.get(term=term, kind='audition', program__degree='B.Mus.', subject__name='Violoncello', program__isnull=False)
        hall = Participation.objects.filter(course__term=term, course__kind='audition', course__video=False, study__program=intake.program, study__subject=intake.subject)
        hall.update(passed=True, grade=20)
        Course.objects.filter(participations__in=hall).update(released=timezone.now())
        advised = recommendations(term, asa)
        passed = [s.advice for s in advised if s.program_id == intake.program_id and s.subject_id == intake.subject_id and s.advice != Study.Status.REJECTED]
        free = min(intake.capacity, len(passed))
        self.assertEqual(passed, [Study.Status.ADMITTED] * free + [Study.Status.WAITLISTED] * (len(passed) - free))
        failed = [s.pk for s in advised if s.advice == Study.Status.REJECTED]
        self.login('asa').post('/aufnahme/bewerbungen/entscheiden/', {'semester': term.pk, 'study': failed})
        self.assertEqual(set(Study.objects.filter(pk__in=failed).values_list('status', flat=True)), {Study.Status.REJECTED})


class MoveUpTest(Seeded):
    def test_an_offer_not_accepted_in_time_goes_down_the_list(self):
        term = Term.current()
        intake, (late,), (behind,) = vacancies(term, Person.objects.get(username='asa'))[0]
        applicant = Client()
        applicant.force_login(late.person)
        applicant.post(f'/aufnahme/meine/{late.pk}/annahme/', {'decision': 'yes', 'insurer': 'BARMER', 'insurance': 'gesetzlich', 'home': '16055', 'external_terms': 0})
        self.assertFalse(late.terms.exists())
        self.login('asa').post(f'/aufnahme/pruefung/{intake.pk}/nachruecken/')
        self.assertEqual(Study.objects.get(pk=late.pk).status, Study.Status.LEFT)
        self.assertEqual(Study.objects.get(pk=behind.pk).status, Study.Status.ADMITTED)
        self.assertTrue(Message.objects.filter(person=behind.person, subject='Bescheid über die Eignungsprüfung: Studienplatzangebot').exists())
        self.assertEqual(vacancies(term, Person.objects.get(username='asa')), [])


class ApplicationTest(Seeded):
    def test_sign_up_and_apply(self):
        anon = Client()
        r = anon.post('/aufnahme/konto/', {
            'first_name': 'Ida', 'last_name': 'Testfall', 'email': 'ida@example.org', 'birth_date': '2005-05-05',
            'password1': 'Geige-Weimar-2026', 'password2': 'Geige-Weimar-2026',
        }, follow=True)
        audition = r.context['form'].fields['audition'].queryset.filter(video=True).first()
        anon.post('/aufnahme/bewerben/', {  # what the account did not ask, the first application does
            'audition': audition.pk, 'repertoire': 'Bach, Partita Nr. 2', 'nationality': '000', 'gender': 'w', 'accompanist': 'on', 'declaration': 'on',
        })
        ida = Person.objects.get(username='ida@example.org')
        study = ida.studies.get()
        self.assertEqual(study.status, Study.Status.APPLIED)
        anon.post(f'/aufnahme/bewerbung/{study.pk}/unterlagen/', {'kind': 'cv', 'file': SimpleUploadedFile('cv.pdf', b'%PDF-1.4', content_type='application/pdf')})
        anon.post(f'/aufnahme/bewerbung/{study.pk}/unterlagen/', upload('vortrag.mp4'))
        self.assertEqual(ida.documents.count(), 2)
        self.assertTrue(Participation.objects.filter(study=study, course=audition).exists())

    def test_declaration_is_required(self):
        c = self.login('bewerbung')
        audition = c.get('/aufnahme/bewerben/').context['form'].fields['audition'].queryset.first()
        self.assertIn('declaration', c.post('/aufnahme/bewerben/', {'audition': audition.pk, 'repertoire': 'x'}).context['form'].errors)


class VideoTest(Seeded):
    """One uncut file per video round, replaced by a new upload; no links; only where the HfM asks for a video."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        c = Client()
        c.force_login(Person.objects.get(username='bewerbung'))
        course = c.get('/aufnahme/bewerben/').context['form'].fields['audition'].queryset.filter(video=True).exclude(subject__name='Violoncello').first()
        c.post('/aufnahme/bewerben/', {'audition': course.pk, 'repertoire': 'Mozart, Konzert', 'declaration': 'on'})
        cls.study = Study.objects.get(person__username='bewerbung', subject=course.subject)

    def setUp(self):
        self.applicant = self.login('bewerbung')
        self.url = f'/aufnahme/bewerbung/{self.study.pk}/unterlagen/'

    def test_checklist(self):
        section = next(s for s in self.applicant.get('/aufnahme/meine/').context['studies'] if s.pk == self.study.pk)
        self.assertEqual([i['label'] for i in section.checklist][:3], ['Video', 'Lebenslauf', 'Abitur- oder letztes Schulzeugnis'])
        no_video = Study.objects.filter(participations__course__program__isnull=False, participations__course__video=False, status='applied').first()
        self.assertNotIn('Video', [i['label'] for i in checklist(no_video)])

    def test_upload_replace_discard(self):
        r = self.applicant.post(self.url, upload('take.mp4'), headers=PANEL)
        self.assertEqual(r.headers['X-Location'], '/aufnahme/meine/')
        self.assertIn('Video bitte als .mp4', flashes(self.applicant.post(self.url, upload('cv.pdf'), follow=True))[-1])
        self.assertIn('Bitte eine Datei wählen.', flashes(self.applicant.post(self.url, {'kind': 'video', 'url': 'https://youtu.be/x'}, follow=True))[-1])
        self.applicant.post(self.url, upload('take2.webm'))
        video = Document.objects.get(study=self.study, kind='video')
        self.assertEqual(video.title, 'take2.webm')
        self.assertTrue(video.playable)
        self.assertIn('<video controls', self.applicant.get('/aufnahme/meine/').text)
        self.applicant.post(f'/aufnahme/unterlagen/{video.pk}/entfernen/')
        self.assertFalse(Document.objects.filter(pk=video.pk).exists())

    def test_upload_limits_come_from_the_type(self):
        DocumentType.objects.filter(key='cv').update(file_types='.pdf', single=True)
        self.assertIn('Lebenslauf bitte als .pdf hochladen.', flashes(self.applicant.post(self.url, upload('cv.docx', 'cv'), follow=True)))
        self.applicant.post(self.url, upload('cv.pdf', 'cv'))
        self.applicant.post(self.url, upload('cv2.pdf', 'cv'))
        self.assertEqual(list(self.study.documents.filter(kind='cv').values_list('title', flat=True)), ['cv2.pdf'])

    def test_what_is_missing_is_handed_in_after_the_deadline_but_not_the_video(self):
        cello = Study.objects.get(person__username='bewerbung', subject__name='Violoncello')
        self.assertIn('nachreichen: Einwilligung der Erziehungsberechtigten', self.applicant.get('/').text)
        url = f'/aufnahme/bewerbung/{cello.pk}/unterlagen/'
        self.applicant.post(url, {'kind': 'consent', 'file': SimpleUploadedFile('einwilligung.pdf', b'%PDF-1.4', content_type='application/pdf')})
        self.assertTrue(cello.documents.filter(kind='consent').exists())
        self.assertIn('Die Frist für Unterlagen ist abgelaufen.', flashes(self.applicant.post(url, upload('neu.mp4'), follow=True)))

    def test_the_form_offers_what_is_left_at_their_level_their_instruments_first(self):
        auditions = self.applicant.get('/aufnahme/bewerben/').context['form'].fields['audition'].queryset
        self.assertFalse(auditions.filter(program=self.study.program, subject=self.study.subject).exists())
        self.assertEqual((auditions[0].subject.institute, auditions[0].program.postgraduate), (self.study.subject.institute, False))

    def test_a_cv_handed_in_once_counts_for_every_application(self):
        cello = Study.objects.get(person__username='bewerbung', subject__name='Violoncello')
        with self.captureOnCommitCallbacks(execute=True):
            cv = Document.objects.create(person=cello.person, study=cello, kind_id='cv', title='lebenslauf.pdf', file=SimpleUploadedFile('lebenslauf.pdf', b'%PDF-1.4'))
        items = {i['label']: i['documents'] for i in checklist(self.study)}
        self.assertIn(cv, items['Lebenslauf'])
        self.assertFalse(items['Einzahlungsbeleg Eignungsprüfungsgebühr'])  # paid for each application
        juror = Client()
        juror.force_login(Course.objects.get(video=True, participations__study=self.study).staff.first())
        self.assertEqual(juror.get(cv.href).status_code, 200)

    def test_others_cannot_upload(self):
        self.assertEqual(self.login('student').post(self.url, {'kind': 'cv', 'url': 'https://x.org/a.pdf'}).status_code, 403)


class CorrectionTest(Seeded):
    def test_the_office_running_auditions_marks_nothing(self):
        video_round = Course.objects.get(title='Videostufe Violine', released=None)
        score = Score.objects.filter(participation__course=video_round).select_related('participation').first()
        p, before = score.participation, score.value
        data = {f'score-{p.pk}-{score.examiner_id}': '1'}
        self.assertEqual(self.login('institut').post(f'/aufnahme/pruefung/{video_round.pk}/', data).status_code, 403)
        self.login('pruefungsausschuss').post(f'/aufnahme/pruefung/{video_round.pk}/', data)
        self.assertNotEqual(Score.objects.get(pk=score.pk).value, before)  # corrections are the Prüfungsausschuss's, logged


class JuryTest(Seeded):
    def test_juror_works_through_the_video_round(self):
        wendt = Person.objects.get(username='lehre')
        juror = self.login('lehre')
        video_round = Course.objects.get(title='Videostufe Violine', released=None)
        self.assertIn(f'/aufnahme/pruefung/{video_round.pk}/sichtung/', juror.get('/').text)
        r = juror.get(f'/aufnahme/pruefung/{video_round.pk}/sichtung/', headers=PANEL)
        first = r.context['p']
        self.assertIn('<video', r.text)
        url = f'/aufnahme/pruefung/{video_round.pk}/sichtung/?bewerbung={first.pk}'
        juror.post(url, {f'score-{first.pk}-{wendt.pk}': '19,5'}, headers=PANEL)
        self.assertEqual(str(Score.objects.get(participation=first, examiner=wendt).value), '19.50')
        refused = juror.post(url, {f'score-{first.pk}-{wendt.pk}': '31'}, headers=PANEL)
        self.assertTrue(refused.context['error'])  # out of range, or locked once the whole jury has marked
        self.assertEqual(str(Score.objects.get(participation=first, examiner=wendt).value), '19.50')
        self.assertEqual(self.login('student').get(f'/aufnahme/pruefung/{video_round.pk}/sichtung/').status_code, 403)

    def test_an_audition_closes_only_with_every_result(self):
        hall = Course.objects.get(term__name='SoSe 2027', title='Eignungsprüfung Hauptfach Violoncello')
        r = self.login('institut').post(f'/aufnahme/pruefung/{hall.pk}/abschluss/', follow=True)
        self.assertTrue(flashes(r)[-1].startswith('Noch ohne Ergebnis: '))
        self.assertIsNone(Course.objects.get(pk=hall.pk).released)

    def test_board_flags_incomplete_applications(self):
        self.assertIn('unvollständig', self.login('asa').get('/aufnahme/bewerbungen/').text)

    def test_students_find_how_to_apply_for_a_master(self):
        r = self.login('student').get('/suche/', {'q': 'master'}, headers={'X-Fragment': '1'})
        self.assertIn('Bewerbung für ein weiteres Studium', r.text)


class ChecklistTest(Seeded):
    """The checklist asks for what the document types say, for whom they say it."""

    def test_conditions(self):
        violin = Study.objects.filter(status='applied', subject__name='Violine').select_related('person', 'program', 'subject').first()
        cello = Study.objects.filter(status='applied', subject__name='Violoncello').select_related('person', 'program', 'subject').first()
        kind = DocumentType.objects.create(name='Probespielliste', condition='subjects', required=True)
        kind.subjects.set([violin.subject])
        self.assertIn('Probespielliste', incomplete(violin))
        self.assertNotIn('Probespielliste', [i['label'] for i in checklist(cello)])
        DocumentType.objects.filter(key='language').update(condition='always', required=True)
        self.assertIn('Sprachnachweis Deutsch', incomplete(cello))
        DocumentType.objects.filter(key='cv').update(condition='never')
        self.assertNotIn('Lebenslauf', [i['label'] for i in checklist(cello)])


class DocumentAccessTest(Seeded):
    """Uploads are no public files: each is opened through a check of who may see it."""

    def test_who_may_open_a_document(self):
        study = Study.objects.filter(status='applied', participations__course__video=True, participations__course__released=None).select_related('person').first()
        juror = Course.objects.filter(video=True, participations__study=study).first().staff.first()
        owner = Client()
        owner.force_login(study.person)
        with self.captureOnCommitCallbacks(execute=True):
            document = Document.objects.create(person=study.person, study=study, kind_id='cv', title='lebenslauf.pdf', file=SimpleUploadedFile('lebenslauf.pdf', b'%PDF-1.4'))
        self.assertNotIn('lebenslauf', document.file.name)
        self.assertEqual(owner.get(document.href).status_code, 200)
        juror_client = Client()
        juror_client.force_login(juror)
        self.assertEqual(juror_client.get(document.href).status_code, 200)
        self.assertEqual(self.login('student').get(document.href).status_code, 403)
        self.assertEqual(self.login('student').get(f'/media/{document.file.name}').status_code, 404)


class SignupTest(Seeded):
    def test_one_account_per_address(self):
        taken = Person.objects.get(username='student').email
        r = Client().post('/aufnahme/konto/', {
            'first_name': 'Zweit', 'last_name': 'Konto', 'email': taken.upper(), 'gender': 'w', 'birth_date': '01.01.2000',
            'password1': 'Geige-Weimar-2026', 'password2': 'Geige-Weimar-2026',
        })
        self.assertIn('email', r.context['form'].errors)

    def test_password_reset(self):
        person = Person.objects.get(username='student')
        Client().post('/konto/password_reset/', {'email': person.email})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/konto/reset/', mail.outbox[0].body)


class RetentionTest(Seeded):
    def test_applications_that_came_to_nothing_are_forgotten_but_still_counted(self):
        study = (
            Study.objects.filter(status='rejected', start_term=Term.current(), documents__isnull=False)
            .annotate(n=Count('person__studies', distinct=True)).filter(n=1).select_related('person').first()
        )
        gender = study.person.gender
        forget(timezone.localdate() + timedelta(days=1))
        person = Person.objects.get(pk=study.person_id)
        self.assertFalse(study.documents.exists())
        self.assertEqual((person.last_name, person.email, person.is_active, person.gender), ('Anonym', '', False, gender))
        self.assertTrue(Study.objects.filter(pk=study.pk).exists())
        self.assertFalse(Message.objects.filter(person=person).exists())
