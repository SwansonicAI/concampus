import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from curriculum.models import Program
from rooms.models import Booking, Room
from students.models import Document, Study
from .access import CODENAMES
from .apps import tile_apps, tiles
from .history import acting, restore
from .models import Change, Message, Office, Person, Term, Text
from .testing import Empty, Seeded, flashes
from .texts import TEXTS, render, send
from .views import found

PANEL = {'X-Panel': '1'}
DEMO = ['admin', 'asa', 'pruefungsamt', 'dekanat', 'veranstaltungsbuero', 'lehre', 'student', 'bewerbung']


class HistoryTest(Empty):
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


class CapabilityTest(Seeded):
    """Offices are named freely; what someone may do comes only from the capabilities their offices hold."""

    def pages(self):
        study = Study.objects.filter(status='enrolled').first()
        return {
            'view_students': '/studierende/liste/',
            'view_files': f'/studierende/{study.pk}/',
            'manage_students': f'/studierende/bescheide/?study={study.pk}',
            'manage_reregistration': '/studierende/rueckmeldung/',
            'message_students': f'/studierende/nachricht/?study={study.pk}',
            'view_applications': '/aufnahme/bewerbungen/',
            'run_auditions': f'/aufnahme/pruefung/{Course.objects.filter(kind="audition").first().pk}/',
            'manage_exams': '/pruefungen/neu/',
            'decide_results': f'/pruefungen/{Course.objects.filter(kind="exam").first().pk}/',
            'recognise': '/pruefungen/anerkennung/',
            'manage_courses': '/lehre/neu/',
            'view_teaching_load': '/lehre/deputat/',
            'roll_over': '/lehre/semesterwechsel/',
            'view_curriculum': '/studiengaenge/',
        }

    def test_each_capability_opens_its_pages_and_nothing_else_does(self):
        clerk = Person.objects.create(username='clerk', last_name='Neu')
        office = Office.objects.create(name='Beliebiger Name')
        clerk.groups.add(office)
        client = Client()
        client.force_login(clerk)
        for capability, page in self.pages().items():
            with self.subTest(capability=capability):
                office.permissions.clear()
                self.assertEqual(client.get(page).status_code, 403)
                office.permissions.set(Permission.objects.filter(codename=capability))
                self.assertEqual(client.get(page).status_code, 200)

    def test_superusers_hold_every_capability_and_roles_still_derive(self):
        self.assertEqual(Person.objects.get(username='admin').roles >= set(CODENAMES), True)
        self.assertEqual(Person.objects.get(username='student').roles, {'student'})
        self.assertIn('teacher', Person.objects.get(username='lehre').roles)
        self.assertEqual(self.login('asa').get('/admin/').status_code, 302)

    def test_no_dead_links(self):
        """Every tile, feature and search hit someone is offered opens for them."""
        for username in [*DEMO, 'campus']:
            person = Person.objects.get(username=username)
            client = self.login(username)
            urls = {app.url and reverse(app.url) for app, _ in tiles(person)}
            urls |= {f['url'] for app in tile_apps() for f in app.features(person)}
            urls |= {h['url'] for q in ['Chen', 'Violine', 'Konzert', 'Klausur', 'FH'] for _, hits in found(person, q, 5) for h in hits}
            for url in urls - {None, reverse('admin:index')}:
                with self.subTest(username=username, url=url):
                    self.assertEqual(client.get(url, follow=True).status_code, 200)

    def test_the_hochschule_is_data_not_code(self):
        """Neither its offices nor its name and address are written into the code; the demo data brings them."""
        root = Path(__file__).resolve().parent.parent
        names = re.compile(r'\b(ASA|Prüfungsamt|Dekanat|Veranstaltungsbüro|HfM|Weimar|Liszt|LISZT|hfm-weimar|Platz der Demokratie|99423)\b')
        branding = {'hfm.css', 'base.html', '_letterhead.html', 'settings.py'}  # the design and the deployment, done separately
        for path in root.glob('*/**/*'):
            if path.suffix in ('.py', '.html', '.js', '.css') and not {'.venv', 'migrations', 'management'} & set(path.parts) and path.name not in branding and 'test' not in path.name:
                with self.subTest(path=str(path.relative_to(root))):
                    self.assertIsNone(names.search(path.read_text(encoding='utf-8')))


class TextTest(Seeded):
    """Letters and messages say what the Hochschule words; sent ones keep their wording."""

    def test_every_text_renders(self):
        for key, spec in TEXTS.items():
            with self.subTest(key=key):
                subject, body = render(key, **{name: f'<{name}>' for name in spec.allowed})
                self.assertNotIn('[', subject + body)

    def test_placeholders_are_checked(self):
        campus = self.login('campus')
        for wrong in ['Hallo {vorname.__class__}', 'Hallo {passwort}', 'Hallo {vorname', 'Hallo {vorname!r}']:
            with self.subTest(wrong=wrong):
                r = campus.post('/einstellungen/texte/willkommen/', {'subject': 'Willkommen', 'body': wrong})
                self.assertTrue(r.context['form'].errors)
        self.assertEqual(self.login('student').get('/einstellungen/texte/').status_code, 403)

    def test_own_wording_then_back_to_standard(self):
        campus = self.login('campus')
        old = Message.objects.filter(subject='Zulassungsbescheid').first()
        before = old.body
        campus.post('/einstellungen/texte/zulassung/', {'subject': 'Ihre Zulassung', 'body': 'Willkommen, {vorname}, zum {semester}.'})
        study = Study.objects.filter(status='applied').exclude(person__username='bewerbung').select_related('person').first()
        self.login('asa').post(f'/aufnahme/bewerbung/{study.pk}/entscheidung/', {'status': 'admitted'})
        letter = Message.objects.filter(person=study.person).first()
        self.assertEqual((letter.subject, letter.body), ('Ihre Zulassung', f'Willkommen, {study.person.first_name}, zum {study.start_term}.'))
        self.assertEqual(letter.sender, 'Akademische und Studentische Angelegenheiten')
        old.refresh_from_db()
        self.assertEqual(old.body, before)
        campus.post('/einstellungen/texte/zulassung/', {'standard': '1'})
        self.assertFalse(Text.objects.filter(key='zulassung').exists())

    def test_mail_goes_out_once_saved(self):
        mei = Person.objects.get(username='student')
        with self.captureOnCommitCallbacks() as pending:
            send('willkommen', mei)
        self.assertEqual(len(mail.outbox), 0)  # rolled back, nothing would go out
        for callback in pending:
            callback()
        self.assertEqual(mail.outbox[0].subject, 'Willkommen')

    def test_refusals_carry_the_appeal(self):
        study = Study.objects.filter(status='applied').exclude(person__username='bewerbung').first()
        self.login('asa').post(f'/aufnahme/bewerbung/{study.pk}/entscheidung/', {'status': 'rejected'})
        self.assertIn('Rechtsbehelfsbelehrung', Message.objects.filter(person=study.person).first().body)


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
        booking = Booking.objects.create(room=Room.objects.create(name='Probenraum', building='Test', capacity=8, function_id='kammermusik'), start=start, end=start + timedelta(hours=2), person=self.mei, category_id='probe', title='Probe Verlauf')
        booking.participants.set([self.mei])
        events.post(f'/raeume/buchung/{booking.pk}/loeschen/')
        gone = Change.objects.get(table='rooms.booking', row=booking.pk, action='deleted')
        self.assertEqual(gone.fields['participants'], [self.mei.pk])
        blocker = Booking.objects.create(room=booking.room, start=start, end=start + timedelta(hours=1), person=self.mei, category_id='probe')
        self.assertIn('Der Raum ist in diesem Zeitraum bereits belegt.', flashes(events.post(f'/verlauf/{gone.pk}/zuruecksetzen/', {'next': '/'}, follow=True)))
        blocker.delete()
        events.post(f'/verlauf/{gone.pk}/zuruecksetzen/', {'next': '/'})
        self.assertEqual(list(Booking.objects.get(pk=booking.pk).participants.all()), [self.mei])

    def test_deleting_a_person_deletes_their_history(self):
        person = Change.objects.filter(table='courses.score').first().person
        total, theirs = Change.objects.count(), Change.objects.filter(person=person).count()
        person.delete()
        self.assertEqual(Change.objects.count(), total - theirs)


class FreshInstallTest(Empty):
    """A Hochschule starting out: no semesters yet, then only the current one. Nothing may fail."""

    PAGES = [
        '/', '/postfach/', '/suche/?q=a', '/studierende/liste/', '/studierende/rueckmeldung/', '/studiengaenge/', '/lehre/verzeichnis/',
        '/lehre/meine/', '/lehre/stundenplan/', '/lehre/deputat/', '/lehre/semesterwechsel/', '/lehre/neu/', '/pruefungen/', '/pruefungen/neu/',
        '/pruefungen/anerkennung/', '/aufnahme/bewerbungen/', '/aufnahme/kommissionen/', '/aufnahme/meine/', '/aufnahme/bewerben/',
        '/raeume/', '/raeume/finden/', '/raeume/buchen/', '/raeume/meine/', '/raeume/veranstaltungen/', '/einstellungen/',
        '/einstellungen/texte/', '/einstellungen/semester/', '/einstellungen/semester/neu/', '/einstellungen/eignungspruefungen/neu/',
    ]

    def visit(self):
        client = Client()
        client.force_login(Person.objects.create(username='it', is_superuser=True))
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertIn(client.get(page, follow=True).status_code, (200, 403))

    def test_without_semesters(self):
        self.visit()

    def test_with_only_the_current_semester(self):
        today = timezone.localdate()
        Term.objects.create(
            name='Jetzt', start=today - timedelta(days=30), end=today + timedelta(days=30), lectures_start=today - timedelta(days=20),
            lectures_end=today + timedelta(days=20), reregistration_start=today, deadline=today + timedelta(days=10), fee=100,
        )
        self.visit()
