from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from courses.models import Course
from curriculum.models import Program
from rooms.models import Booking, Room
from students.models import Document, Study
from .history import acting, restore
from .models import Change, Person
from .testing import Seeded, flashes

PANEL = {'X-Panel': '1'}
DEMO = ['admin', 'asa', 'pruefungsamt', 'dekanat', 'veranstaltungsbuero', 'lehre', 'student', 'bewerbung']


class HistoryTest(TestCase):
    def test_changes_are_kept_as_differences_and_undone_only_while_current(self):
        clerk = Person.objects.create(username='clerk')
        Person.objects.create(username='mei', last_name='Chen', address='Am Horn 1')
        mei = Person.objects.get(username='mei')
        mei.address, mei.password = 'Jakobstraße 7', 'secret'
        with acting(clerk):
            mei.save()
            mei.save()  # nothing changed: nothing kept

        change = Change.objects.get(action='changed')
        self.assertEqual((change.by, change.person), (clerk, mei))
        self.assertEqual(change.fields, {'address': ['Am Horn 1', 'Jakobstraße 7'], 'password': [None, None]})

        restore(change)
        mei.refresh_from_db()
        self.assertEqual((mei.address, mei.password), ('Am Horn 1', 'secret'))
        self.assertEqual(Change.objects.first().action, 'restored')
        with self.assertRaises(ValidationError):  # already undone: its 'after' is no longer current
            restore(change)


class SmokeTest(Seeded):
    """Every page opens, or is refused, for every demo account; none fails."""

    def pages(self):
        exam = Course.objects.filter(kind='exam', title__contains='Wiederholung').first()
        audition = Course.objects.filter(kind='audition', title='Videostufe Violine', released=None).first()
        lesson = Course.objects.filter(kind='lesson').first()
        student = Study.objects.get(person__username='student')
        applicant = Study.objects.get(person__username='bewerbung')
        admitted = Study.objects.filter(status='admitted').first()
        booking = Booking.objects.filter(course=None).first()
        room = Room.objects.get(name='FH 310')
        return [
            '/', '/postfach/', '/studierende/', '/studierende/liste/', '/studierende/liste/?format=csv', '/studierende/rueckmeldung/',
            f'/studierende/{student.pk}/', f'/studierende/{student.pk}/bescheinigung/', '/studiengaenge/',
            *[f'/studiengaenge/{p.pk}/' for p in Program.objects.all()],
            '/lehre/', '/lehre/verzeichnis/', '/lehre/meine/', '/lehre/stundenplan/', '/lehre/deputat/', '/lehre/semesterwechsel/',
            f'/lehre/{lesson.pk}/', '/lehre/neu/', '/pruefungen/', f'/pruefungen/{exam.pk}/', '/pruefungen/anerkennung/',
            f'/pruefungen/leistungen/{student.pk}/', '/pruefungen/neu/', f'/pruefungen/{exam.pk}/bearbeiten/',
            '/aufnahme/', '/aufnahme/bewerbungen/', '/aufnahme/kommissionen/', '/aufnahme/meine/', '/aufnahme/bewerben/',
            f'/aufnahme/pruefung/{audition.pk}/', f'/aufnahme/pruefung/{audition.pk}/sichtung/',
            f'/aufnahme/bewerbung/{applicant.pk}/', f'/aufnahme/bewerbung/{admitted.pk}/',
            '/raeume/', '/raeume/?raeume=alle', '/raeume/veranstaltungen/', f'/raeume/{room.pk}/', '/raeume/meine/', '/raeume/buchen/',
            f'/raeume/buchung/{booking.pk}/', '/raeume/finden/', '/suche/?q=Violine',
            f'/verlauf/core.person/{student.person_id}/', f'/verlauf/courses.course/{exam.pk}/',
            *[f'/postfach/{d.message_id}/brief/' for d in Document.objects.filter(message__isnull=False)[:3]],
        ]

    def test_every_page_for_every_account(self):
        pages = self.pages()
        for username in DEMO:
            client = self.login(username)
            for page in pages:
                with self.subTest(username=username, page=page):
                    self.assertIn(client.get(page).status_code, (200, 302, 403))

    def test_strangers_are_sent_to_the_login(self):
        self.assertEqual(Client().get('/konto/login/').status_code, 200)
        self.assertEqual(Client().get('/').status_code, 302)


class SearchTest(Seeded):
    def test_palette_finds_features_dates_and_people(self):
        student = self.login('student')
        r = student.get('/suche/', {'q': 'raum'}, headers={'X-Fragment': '1'})
        self.assertIn('Raum buchen', r.text)
        self.assertNotIn('<html', r.text)
        self.assertIn('Freie Räume am', student.get('/suche/', {'q': 'fr 14'}, headers={'X-Fragment': '1'}).text)
        self.assertIn('Köhler', self.login('asa').get('/suche/', {'q': 'KOEHLER'}, headers={'X-Fragment': '1'}).text)
        self.assertTrue(student.get('/datum/', {'q': 'nächsten fr 9'}).json()['iso'])

    def test_side_panel(self):
        student = self.login('student')
        booking = Booking.objects.filter(person__username='student').first()
        r = student.get(f'/raeume/buchung/{booking.pk}/', headers=PANEL)
        self.assertNotIn('appbar', r.text)
        r = student.post('/raeume/buchen/', {'room': 1, 'category': 'uebung', 'day': 'quatsch', 'begin': '9', 'until': '10'}, headers=PANEL)
        self.assertIn('Datum nicht erkannt', r.text)


class HistoryFlowTest(Seeded):
    """What the offices see in the Verlauf panel, and restoring from it."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mei = Person.objects.get(username='student')
        cls.url = f'/verlauf/core.person/{cls.mei.pk}/'

    def test_each_office_sees_its_own_records(self):
        self.assertEqual({c.table for c in self.login('asa').get(self.url, headers=PANEL).context['changes']}, {'core.person', 'courses.participation'})
        self.assertEqual({c.table for c in self.login('pruefungsamt').get(self.url, headers=PANEL).context['changes']}, {'courses.participation'})
        self.assertFalse(self.login('veranstaltungsbuero').get(self.url, headers=PANEL).context['changes'])
        self.assertEqual(self.login('student').get(self.url).status_code, 403)

    def test_marks_entered_in_bulk_are_kept_once(self):
        wendt = Person.objects.get(username='lehre')
        course = Course.objects.filter(video=True, released=None, staff=wendt).first()
        p = course.participations.exclude(scores__examiner=wendt).select_related('study').first()
        juror = self.login('lehre')
        url = f'/aufnahme/pruefung/{course.pk}/sichtung/?bewerbung={p.pk}'
        juror.post(url, {f'score-{p.pk}-{wendt.pk}': '17,5'})
        juror.post(url, {f'score-{p.pk}-{wendt.pk}': '18'})
        juror.post(url, {f'score-{p.pk}-{wendt.pk}': '18'})
        marks = Change.objects.filter(table='courses.score', by=wendt, person=p.study.person)
        self.assertEqual([m.action for m in marks], ['changed', 'created'])
        self.assertEqual(marks[0].fields, {'value': ['17.50', '18']})

    def test_restore_conflict_and_undo(self):
        asa = self.login('asa')
        address = Change.objects.get(table='core.person', row=self.mei.pk, action='changed')
        self.assertEqual(self.login('pruefungsamt').post(f'/verlauf/{address.pk}/zuruecksetzen/').status_code, 403)
        self.assertEqual(flashes(asa.post(f'/verlauf/{address.pk}/zuruecksetzen/', {'next': self.url}, follow=True)), ['Zurückgesetzt.'])
        self.mei.refresh_from_db()
        self.assertEqual(self.mei.address, address.fields['address'][0])
        self.assertIn('Seitdem erneut geändert', flashes(asa.post(f'/verlauf/{address.pk}/zuruecksetzen/', {'next': self.url}, follow=True))[0])
        undo = Change.objects.filter(table='core.person', row=self.mei.pk).first()
        r = asa.post(f'/verlauf/{undo.pk}/zuruecksetzen/', {'next': self.url}, headers=PANEL)
        self.assertEqual(r.headers['X-Location'], self.url)
        self.mei.refresh_from_db()
        self.assertEqual(self.mei.address, address.fields['address'][1])

    def test_deleted_booking_comes_back_unless_its_room_is_taken(self):
        events = self.login('veranstaltungsbuero')
        start = timezone.now() + timedelta(days=3)
        booking = Booking.objects.create(room=Room.objects.create(name='Probenraum', building='Test', capacity=8), start=start, end=start + timedelta(hours=2), person=self.mei, category='probe', title='Probe Verlauf')
        booking.participants.set([self.mei])
        events.post(f'/raeume/buchung/{booking.pk}/loeschen/')
        gone = Change.objects.get(table='rooms.booking', row=booking.pk, action='deleted')
        self.assertEqual(gone.fields['participants'], [self.mei.pk])
        blocker = Booking.objects.create(room=booking.room, start=start, end=start + timedelta(hours=1), person=self.mei, category='probe')
        self.assertIn('Der Raum ist in diesem Zeitraum bereits belegt.', flashes(events.post(f'/verlauf/{gone.pk}/zuruecksetzen/', {'next': '/'}, follow=True)))
        blocker.delete()
        events.post(f'/verlauf/{gone.pk}/zuruecksetzen/', {'next': '/'})
        self.assertEqual(list(Booking.objects.get(pk=booking.pk).participants.all()), [self.mei])

    def test_deleting_a_person_deletes_their_history(self):
        person = Change.objects.filter(table='courses.score').first().person
        total, theirs = Change.objects.count(), Change.objects.filter(person=person).count()
        person.delete()
        self.assertEqual(Change.objects.count(), total - theirs)
