from core.apps import tiles
from core.models import Message, Person, Term
from core.testing import Seeded, flashes
from .models import Document, Study, StudyTerm


class EnrolmentTest(Seeded):
    def test_enrolment_waits_for_the_insurers_notice(self):
        asa = self.login('asa')
        study = next(s for s in Study.objects.filter(status='admitted') if s.missing() == ['Versicherungsmeldung der Krankenkasse fehlt'])
        self.assertIn('Versicherungsmeldung der Krankenkasse fehlt', study.missing())
        self.assertIn('Versicherungsmeldung', flashes(asa.post(f'/studierende/{study.pk}/immatrikulieren/', follow=True))[0])
        asa.post(f'/studierende/{study.pk}/versicherung/')
        asa.post(f'/studierende/{study.pk}/immatrikulieren/')
        study.refresh_from_db()
        self.assertEqual(study.status, Study.Status.ENROLLED)
        self.assertTrue(study.person.matriculation)
        self.assertTrue(Document.objects.filter(study=study, title='Immatrikulationsbescheid').exists())

    def test_bescheide_print_as_letters_for_their_recipient_and_the_office(self):
        notice = Document.objects.filter(kind='notice', study__status='rejected').select_related('person').first()
        r = self.login('asa').get(notice.href)
        self.assertIn(notice.person.legal_name, r.text)
        self.assertIn('Rechtsbehelfsbelehrung', r.text)
        self.assertEqual(self.login('student').get(notice.href).status_code, 403)
        rejected = Study.objects.filter(status='rejected')[:3]
        r = self.login('asa').get('/studierende/bescheide/', {'study': [s.pk for s in rejected]})
        self.assertEqual(r.text.count('class="document letter"'), 3)


class RosterTest(Seeded):
    def test_filters_selection_and_message(self):
        asa = self.login('asa')
        r = asa.get('/studierende/liste/', {'fs': '1'})
        self.assertEqual({s.fs for s in r.context['studies']}, {1})
        chosen = [s.pk for s in r.context['studies'][:3]]
        before = Message.objects.count()
        r = asa.post('/studierende/nachricht/', {'study': chosen, 'subject': 'Begrüßung', 'body': 'Willkommen!', 'next': '/studierende/liste/?fs=1'})
        self.assertEqual((r['Location'], Message.objects.count() - before), ('/studierende/liste/?fs=1', 3))
        r = asa.post('/studierende/nachricht/', {'study': chosen[:1], 'subject': 'x', 'body': 'y', 'next': 'https://evil.example/'})
        self.assertFalse(r['Location'].startswith('http'))

    def test_search_by_birth_name(self):
        study = Study.objects.filter(status='enrolled', person__birth_name__gt='').first()
        self.assertIn(study, self.login('asa').get('/studierende/liste/', {'q': study.person.birth_name}).context['studies'])


class ReregistrationTest(Seeded):
    def test_payment_from_someone_blocked_says_what_is_missing(self):
        asa = self.login('asa')
        self.assertTrue(asa.get('/studierende/rueckmeldung/').context['blocked'])
        held = Person.objects.exclude(hold='').first()
        asa.post('/studierende/rueckmeldung/', {'pay': '1', 'person': [held.pk]})
        self.assertIn('Sperre', held.messages.first().body)
        self.assertTrue(StudyTerm.objects.filter(study__person=held, term=Term.upcoming(), paid__isnull=False).exists())


class FileTest(Seeded):
    def test_student_file_shows_what_the_office_needs(self):
        study = Study.objects.filter(status='enrolled', focus__isnull=False, person__external_terms__gt=0).select_related('focus').first()
        r = self.login('asa').get(f'/studierende/{study.pk}/')
        for text in ['Geschlecht', 'Hochschulzugang', 'Krankenversicherung', 'Hochschulsemester', f'Schwerpunkt {study.focus.title}']:
            self.assertIn(text, r.text)

    def test_each_sees_what_their_task_needs(self):
        """docs/privacy.md: the Hauptfachlehrer:in the record, the office deciding results also identity, the jury only the
        performance."""
        wendt = Person.objects.get(username='lehre')
        study = Study.objects.filter(status='enrolled', teacher=wendt).first()
        study.terms.filter(pk=study.terms.order_by('term__start').first().pk).update(leave=True, leave_reason='1')  # Erkrankung
        teacher = self.login('lehre').get(f'/studierende/{study.pk}/').text
        exams = self.login('pruefungsamt').get(f'/studierende/{study.pk}/').text
        self.assertIn('Urlaubssemester', teacher)
        for hidden in ['Krankenversicherung', 'Staatsangehörigkeit', 'Geboren', 'Erkrankung']:
            self.assertNotIn(hidden, teacher)
        self.assertIn('Geboren', exams)
        self.assertNotIn('Krankenversicherung', exams)
        applicant = Study.objects.filter(status='applied', participations__course__staff=wendt).exclude(person__phone='').select_related('person').first()
        jury = self.login('lehre').get(f'/aufnahme/bewerbung/{applicant.pk}/').text
        self.assertNotIn(applicant.person.phone, jury)
        self.assertNotIn('Abitur- oder letztes Schulzeugnis', jury)

    def test_programmes_beyond_the_bachelor(self):
        degrees = set(Study.objects.values_list('program__degree', flat=True))
        self.assertTrue({'B.Mus.', 'M.Mus.', 'B.Ed.', 'M.Ed.', 'Konzertexamen', 'Dr. phil.'} <= degrees)
        self.assertTrue(Study.objects.filter(status='guest').exists())
        jena = Study.objects.filter(program__degree='B.Ed.', participations__course__kind='recognition').first()
        self.assertTrue(sum(m.earned for m in jena.progress()[0]))


class AlumniTest(Seeded):
    def test_graduates_keep_their_study_file(self):
        person = Person.objects.get(username='absolvent')
        study, client = person.studies.get(), self.login('absolvent')
        self.assertEqual([title for _, title in tiles(person)], ['Mein Studium'])
        self.assertEqual(client.get(f'/studierende/{study.pk}/studienzeiten/').context['count'], study.program.semesters)
        self.assertTrue(client.get(f'/pruefungen/leistungen/{study.pk}/').context['copy'])
        self.assertContains(client.get(f'/pruefungen/leistungen/{study.pk}/diploma-supplement/'), 'Copy for information')
