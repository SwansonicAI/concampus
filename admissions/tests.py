from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from core.models import Message, Person
from core.testing import Seeded, flashes
from courses.models import Course, Participation, Score
from students.models import Document, DocumentType, Study, StudyTerm
from .views import checklist, incomplete

PANEL = {'X-Panel': '1'}


def upload(name, kind='video'):
    return {'kind': kind, 'file': SimpleUploadedFile(name, b'\x00\x00\x00\x18ftypmp42', content_type='video/mp4')}


class DecisionTest(Seeded):
    def test_decide_accept_enrol(self):
        asa = self.login('asa')
        study = Study.objects.filter(status='applied').exclude(person__username='bewerbung').first()
        asa.post(f'/aufnahme/bewerbung/{study.pk}/entscheidung/', {'status': 'admitted'})
        self.assertTrue(Message.objects.filter(person=study.person, subject='Zulassungsbescheid').exists())
        applicant = Client()
        applicant.force_login(study.person)
        self.assertTrue(flashes(applicant.post(f'/aufnahme/meine/{study.pk}/annahme/', {'decision': 'yes'}, follow=True)))
        applicant.post(f'/aufnahme/meine/{study.pk}/annahme/', {'decision': 'yes', 'insurer': 'BARMER', 'insurance': 'gesetzlich'})
        study.person.refresh_from_db()
        self.assertEqual(study.person.insurer, 'BARMER')
        self.assertTrue(StudyTerm.objects.filter(study=study).exists())
        asa.post(f'/studierende/{study.pk}/versicherung/')
        asa.post(f'/studierende/{study.pk}/immatrikulieren/')
        study.refresh_from_db()
        self.assertEqual(study.status, Study.Status.ENROLLED)


class ApplicationTest(Seeded):
    def test_sign_up_and_apply(self):
        anon = Client()
        r = anon.post('/aufnahme/konto/', {
            'first_name': 'Ida', 'last_name': 'Testfall', 'email': 'ida@example.org', 'gender': 'w', 'birth_date': '2005-05-05',
            'birth_place': 'Erfurt', 'nationality': 'deutsch', 'password1': 'Geige-Weimar-2026', 'password2': 'Geige-Weimar-2026',
        }, follow=True)
        audition = r.context['form'].fields['audition'].queryset.filter(video=True).first()
        anon.post('/aufnahme/bewerben/', {'audition': audition.pk, 'repertoire': 'Bach, Partita Nr. 2', 'accompanist': 'on', 'declaration': 'on'})
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

    def test_others_cannot_upload(self):
        self.assertEqual(self.login('student').post(self.url, {'kind': 'cv', 'url': 'https://x.org/a.pdf'}).status_code, 403)


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
        self.assertIn('Bitte Punkte zwischen 0 und 25 eintragen.', flashes(juror.post(url, {f'score-{first.pk}-{wendt.pk}': '31'}, follow=True)))
        self.assertEqual(self.login('student').get(f'/aufnahme/pruefung/{video_round.pk}/sichtung/').status_code, 403)

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
