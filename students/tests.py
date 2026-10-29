from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from core.apps import tiles
from core.models import Message, Person, Term
from core.testing import Seeded, flashes
from exams.views import exhausted
from .models import Document, Study, StudyTerm


class EnrolmentTest(Seeded):
    def test_enrolment_waits_for_the_insurers_notice(self):
        asa = self.login('asa')
        study = next(s for s in Study.objects.filter(status='admitted') if s.missing() == ['Versicherungsmeldung der Krankenkasse fehlt'])
        self.assertIn('Versicherungsmeldung der Krankenkasse fehlt', study.missing())
        self.assertIn('Versicherungsmeldung', flashes(asa.post(f'/studierende/{study.pk}/immatrikulieren/', follow=True))[0])
        asa.post(f'/studierende/{study.pk}/versicherung/')
        asa.post(f'/studierende/{study.pk}/immatrikulieren/', {'bestaetigt': '1'})
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
        asa.post('/studierende/rueckmeldung/', {'pay': '1', 'person': [held.pk], 'reference': 'Barzahlung'})
        self.assertIn('Sperre', held.messages.first().body)
        self.assertTrue(StudyTerm.objects.filter(study__person=held, term=Term.upcoming(), paid__isnull=False).exists())

    def test_the_bank_statement_books_whom_it_names_with_the_full_fee(self):
        term, asa = Term.upcoming(), self.login('asa')
        full, short, camt = Person.objects.filter(studies__status='enrolled').exclude(
            pk__in=StudyTerm.objects.filter(term=term, paid__isnull=False).values('study__person'),
        ).distinct()[:3]
        day, fee, less = f'{term.deadline:%d.%m.%Y}', f'{term.fee:.2f}'.replace('.', ','), f'{term.fee - 1:.2f}'.replace('.', ',')
        csv = '\n'.join([
            'Buchungstag;Valuta;Verwendungszweck;Betrag;Währung',
            f'{day};{day};SEMESTERBEITRAG {full.matriculation} {full.last_name};{fee};EUR',
            f'{day};{day};Beitrag {short.matriculation};{less};EUR',
            f'{day};{day};Spende;50,00;EUR',
            f'{day};{day};Rueckzahlung {camt.matriculation};-{fee};EUR',  # outgoing: no payment
        ])
        r = asa.post('/studierende/rueckmeldung/', {'statement': SimpleUploadedFile('umsaetze.csv', csv.encode())}, follow=True)
        self.assertEqual(flashes(r), [f'1 Zahlung verbucht. Zu wenig überwiesen: {short.name} ({less} €). 1 Buchung ohne offene Matrikelnummer im Verwendungszweck.'])
        camt053 = f'''<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>
            <Ntry><Amt Ccy="EUR">{term.fee}</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>{term.deadline:%Y-%m-%d}</Dt></BookgDt>
                <NtryDtls><TxDtls><RmtInf><Ustrd>Semesterbeitrag</Ustrd><Ustrd>Matr. {camt.matriculation}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
            <Ntry><Amt Ccy="EUR">{term.fee}</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>{term.deadline:%Y-%m-%d}</Dt></BookgDt></Ntry>
        </Stmt></BkToCstmrStmt></Document>'''
        r = asa.post('/studierende/rueckmeldung/', {'statement': SimpleUploadedFile('auszug.xml', camt053.encode())}, follow=True)
        self.assertEqual(flashes(r), ['1 Zahlung verbucht.'])
        booked = set(StudyTerm.objects.filter(term=term, paid=term.deadline).values_list('study__person', flat=True))
        self.assertEqual(booked & {full.pk, short.pk, camt.pk}, {full.pk, camt.pk})


class LeaveTest(Seeded):
    """Urlaubssemester for the coming term: students ask within the Rückmeldung, for at most two, and in the first
    Fachsemester only for illness or Mutterschutz; the office decides beyond that."""

    def test_students_within_the_rules_the_office_beyond(self):
        term, today = Term.upcoming(), timezone.localdate()
        Term.objects.filter(pk=term.pk).update(reregistration_start=today - timedelta(days=14), deadline=today - timedelta(days=1), late_deadline=None)
        new, old = (next(s for s in Study.objects.filter(status='enrolled', prior_terms=0).select_related('person') if s.semester == n) for n in (1, 3))

        def ask(study, reason, client=None):
            r = (client or self.login(study.person.username)).post(f'/studierende/{study.pk}/urlaub/', {'reason': reason}, follow=True)
            return flashes(r)[0]

        self.assertIn('lassen sich vom', ask(new, '1'))  # the window is over
        Term.objects.filter(pk=term.pk).update(reregistration_start=today, deadline=today)
        new.terms.filter(term__start__lte=today).update(leave=True, leave_reason='1')  # ill all their first term: the coming one is their first Fachsemester
        self.assertIn('nur bei Erkrankung', ask(new, '4'))
        self.assertIn('Urlaubssemester eingetragen', ask(new, '1'))
        old.terms.filter(pk__in=old.terms.filter(term__start__lte=today).values('pk')[:2]).update(leave=True, leave_reason='4')
        self.assertIn('höchstens 2 Urlaubssemester', ask(old, '4'))
        self.assertIn('Urlaubssemester eingetragen', ask(old, '4', self.login('asa')))
        self.assertEqual({s: StudyTerm.objects.get(study=s, term=term).leave for s in (new, old)}, {new: True, old: True})


class ExmatriculationTest(Seeded):
    def test_on_a_day_of_the_term_for_a_ground_with_its_evidence(self):
        asa, term, study = self.login('asa'), Term.current(), Study.objects.get(person__username='student')
        url = f'/studierende/{study.pk}/exmatrikulieren/'
        self.assertEqual(flashes(asa.post(url, {'reason': '8'}, follow=True)), ['Kein endgültig nicht bestandenes Modul.'])
        self.assertIn('Bitte einen Tag vom', flashes(asa.post(url, {'reason': '6', 'zum': f'{term.end + timedelta(days=1):%d.%m.%Y}'}, follow=True))[0])
        self.assertContains(asa.post(url, {'reason': '6', 'zum': f'{term.end:%d.%m.%Y}'}), 'Exmatrikulationsbescheid')  # first the preview
        self.assertEqual(Study.objects.get(pk=study.pk).status, Study.Status.ENROLLED)
        asa.post(url, {'reason': '6', 'zum': f'{term.end:%d.%m.%Y}', 'bestaetigt': '1'})
        study.refresh_from_db()
        self.assertEqual((study.status, study.exit_reason, study.since), (Study.Status.LEFT, '6', term.end))
        self.assertTrue(study.documents.filter(kind='notice', title='Exmatrikulationsbescheid').exists())

    def test_the_laufzettel_names_what_is_open_and_a_final_fail_leaves_when_final(self):
        asa = self.login('asa')
        borrowing = Study.objects.filter(status='enrolled', person__loans__isnull=False, person__loans__returned=None).first()
        self.assertTrue(any(what.startswith('Leihinstrument') for what, _ in asa.get(borrowing.get_absolute_url()).context['leaving']))
        lost = next(s for s in Study.objects.filter(status='enrolled') if exhausted(s))
        page = asa.get(lost.get_absolute_url())
        self.assertEqual(page.context['leaving_day'], max(max(m.appeal for m in exhausted(lost)) + timedelta(days=1), Term.current().start - timedelta(days=1)))
        self.assertNotIn('überweisen', ' '.join(t['text'] for t in self.client_for(lost.person).get('/').context['tasks']))  # no Rückmeldung once the claim is lost

    def client_for(self, person):
        client = Client()
        client.force_login(person)
        return client


class FocusTest(Seeded):
    def test_the_student_chooses_on_advice_and_keeps_it_once_begun(self):
        study = Study.objects.get(person__username='student')
        options = study.program.choices()
        other = next(o for o in options if o.pk != study.advised_id)
        student = self.login('student')
        self.assertIn(f'{options[0].choice.title} wählen', student.get('/').text)
        self.assertEqual(self.login('institut').post(f'/studierende/{study.pk}/empfehlung/', {'advised': other.pk}).status_code, 403)
        self.assertContains(student.post(f'/studierende/{study.pk}/schwerpunkt/', {'focus': study.advised_id}), 'Verbindlich wählen')  # first named once more
        student.post(f'/studierende/{study.pk}/schwerpunkt/', {'focus': study.advised_id, 'bestaetigt': '1'})
        student.post(f'/studierende/{study.pk}/schwerpunkt/', {'focus': other.pk, 'bestaetigt': '1'})  # under way: only the office changes it
        study.refresh_from_db()
        self.assertEqual(study.focus_id, study.advised_id)
        self.assertTrue(study.person.messages.filter(subject__startswith='Schwerpunkt').exists())


class FileTest(Seeded):
    def test_student_file_shows_what_the_office_needs(self):
        study = Study.objects.filter(status='enrolled', focus__isnull=False, person__external_terms__gt=0).select_related('focus').first()
        r = self.login('asa').get(f'/studierende/{study.pk}/')
        for text in ['Geschlecht', 'Hochschulzugang', 'Krankenversicherung', 'Hochschulsemester', f'Schwerpunkt {study.focus.title}']:
            self.assertIn(text, r.text)
        self.assertNotIn('Gesamtnote bisher', r.text)  # outcomes, not grades

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
