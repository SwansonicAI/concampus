import base64
import json
import re
import tempfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.serializers.json import DjangoJSONEncoder
from django.core import mail
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from onelogin.saml2.utils import OneLogin_Saml2_Utils as Utils

from courses.models import Course, Score
from curriculum.models import Program
from rooms.models import Booking, Room
from students.models import Document, Study
from . import dates, sso
from .access import CODENAMES
from .apps import tile_apps, tiles
from .history import acting, restore
from .models import Change, Institute, Institution, Message, Office, Person, Term, Text
from .testing import Empty, Seeded, flashes
from .transfer import FORMAT, dump, everything, load
from .texts import TEXTS, render, send
from .views import found, ticket

PANEL = {'X-Panel': '1'}


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
    """Every page opens, or is refused, for every demo account; none fails. What someone is offered opens for them."""

    accounts = ['admin', 'asa', 'pruefungsamt']

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
            f'/raeume/buchung/{booking.pk}/', '/raeume/?von=10:00&personen=4',
            f'/verlauf/core.person/{student.person_id}/', f'/verlauf/courses.course/{exam.pk}/',
            *[f'/postfach/{d.message_id}/brief/' for d in Document.objects.filter(message__isnull=False)[:3]],
        ]

    def offered(self, person):
        """Every tile, feature and search hit the person is offered; of the hits, two of each kind the words find."""
        urls = {app.url and reverse(app.url) for app, _ in tiles(person)}
        urls |= {f['url'] for app in tile_apps() for f in app.features(person)}
        urls |= {h['url'] for q in ['Chen', 'Violine'] for _, hits in found(person, q, 2) for h in hits if not h['url'].startswith('mailto:')}  # colleagues are written to
        return urls - {None, reverse('admin:index')}

    def test_every_page_for_every_account(self):
        pages = self.pages()
        for username in self.accounts:
            client, offered = self.login(username), self.offered(Person.objects.get(username=username))
            for page in sorted(offered):
                with self.subTest(username=username, page=page):
                    self.assertEqual(client.get(page, follow=True).status_code, 200)
            for page in [p for p in pages if p not in offered]:
                with self.subTest(username=username, page=page):
                    self.assertIn(client.get(page).status_code, (200, 302, 403))


class OfficeSmokeTest(SmokeTest):
    """The same for the other offices, in a process of its own."""

    accounts = ['dekanat', 'institut', 'veranstaltungsbuero', 'campus', 'praesidium']


class PeopleSmokeTest(SmokeTest):
    """The same for teachers, students, applicants and graduates."""

    accounts = ['lehre', 'korrepetition', 'student', 'promotion', 'bewerbung', 'absolvent']


class LoginTest(Seeded):
    def test_strangers_are_sent_to_the_login(self):
        self.assertEqual(Client().get('/konto/login/').status_code, 200)
        self.assertEqual(Client().get('/').status_code, 302)

    def test_five_failures_hold_a_name_back_from_that_address(self):
        mei = Person.objects.get(username='student')
        mei.set_password('Geige-Weimar-2026')
        mei.save()
        client = Client()
        for _ in range(5):
            client.post('/konto/login/', {'username': 'student', 'password': 'falsch'})
        r = client.post('/konto/login/', {'username': mei.email, 'password': 'Geige-Weimar-2026'})  # by e-mail too
        self.assertContains(r, 'Fehlversuchen')
        r = Client(REMOTE_ADDR='192.0.2.7').post('/konto/login/', {'username': 'student', 'password': 'Geige-Weimar-2026'})
        self.assertEqual(r.status_code, 302)  # from elsewhere it goes on
        self.assertEqual(list(mei.sign_ins.values_list('success', flat=True)), [True] + [False] * 5)

    def test_a_stale_form_offers_its_page_again(self):
        r = Client(enforce_csrf_checks=True).post('/konto/login/', {'username': 'student'}, HTTP_REFERER='http://testserver/konto/login/')
        self.assertContains(r, 'href="http://testserver/konto/login/"', status_code=403)

    def test_demo_logins_only_past_the_code_and_only_for_its_people(self):
        client = Client()
        self.assertEqual(client.post('/demo/', {'konto': 'student'}).status_code, 404)  # no code set: no way in
        with self.settings(DEMO_CODE='Notenpult'):
            self.assertRedirects(client.get('/konto/login/'), '/zugang/?next=/konto/login/', fetch_redirect_response=False)
            client.post('/zugang/', {'code': 'Pult'})
            self.assertEqual(client.post('/demo/', {'konto': 'student'}).status_code, 302)  # still at the gate
            client.post('/zugang/', {'code': ' notenpult '})
            self.assertEqual(client.post('/demo/', {'konto': 'admin'}).status_code, 404)
            with self.settings(DEMO=[]):
                self.assertEqual(client.post('/demo/', {'konto': 'student'}).status_code, 404)
            client.post('/demo/', {'konto': 'student'})
            self.assertEqual(client.get('/').context['user'].username, 'student')


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
            'view_statistics': '/auswertungen/',
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
        self.assertLessEqual(set(CODENAMES), Person.objects.get(username='admin').roles)
        self.assertEqual(Person.objects.get(username='student').roles, {'student'})
        self.assertIn('teacher', Person.objects.get(username='lehre').roles)
        self.assertEqual(self.login('asa').get('/admin/').status_code, 302)

    def test_the_hochschule_is_data_not_code(self):
        """Neither its offices nor its name and address are written into the code; the demo data brings them."""
        root = Path(__file__).resolve().parent.parent
        names = re.compile(r'\b(ASA|Prüfungsamt|Dekanat|Veranstaltungsbüro|HfM|Weimar|Liszt|LISZT|hfm-weimar|Platz der Demokratie|99423)\b')
        for path in root.glob('*/**/*'):  # the official key lists name every Hochschule
            if path.suffix in ('.py', '.html', '.js', '.css') and not {'.venv', 'migrations', 'management'} & set(path.parts) and path.name != 'destatis.py' and 'test' not in path.name:
                with self.subTest(path=str(path.relative_to(root))):
                    self.assertIsNone(names.search(path.read_text(encoding='utf-8')))


class ReachTest(Seeded):
    """An office limited to an Institut reads and changes the records of that Institut only."""

    def test_an_institute_office_keeps_to_its_institute(self):
        strings, client = 'Streichinstrumente und Harfe', self.login('institut')
        studies = client.get('/studierende/liste/').context['studies']
        self.assertTrue(studies and all(s.subject.institute.name == strings for s in studies))
        other = Study.objects.filter(status='enrolled').exclude(subject__institute__name=strings).first()
        self.assertEqual(client.get(f'/studierende/{other.pk}/').status_code, 403)
        own, foreign = Room.objects.filter(institutes__name=strings).first(), Room.objects.exclude(institutes__name=strings).first()
        self.assertEqual(client.get(f'/einstellungen/raeume/{own.pk}/').status_code, 200)
        self.assertEqual(client.get(f'/einstellungen/raeume/{foreign.pk}/').status_code, 404)
        piano = Institute.objects.get(name='Klavier')
        r = client.post('/einstellungen/raeume/neu/', {'name': 'X 1', 'building': 'X', 'function': own.function_id, 'institutes': [piano.pk], 'capacity': 2})
        self.assertIn('institutes', r.context['form'].errors)  # only its own Institut is offered
        self.assertFalse(Room.objects.filter(name='X 1').exists())

    def test_rights_for_the_whole_hochschule_cannot_be_limited(self):
        r = self.login('campus').post('/einstellungen/aemter/neu/', {
            'name': 'Institutssekretariat Klavier', 'institutes': [Institute.objects.get(name='Klavier').pk], 'capabilities': ['view_students', 'manage_terms'],
        })
        self.assertIn('Nur für die ganze Hochschule', str(r.context['form'].errors))


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
        old = Message.objects.filter(subject='Bescheid über die Eignungsprüfung: Studienplatzangebot').first()
        before = old.body
        campus.post('/einstellungen/texte/zulassung/', {'subject': 'Ihre Zulassung', 'body': 'Willkommen, {vorname}, zum {semester}.'})
        study = Study.objects.filter(status='applied', participations=None).exclude(person__username='bewerbung').select_related('person').first()
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
        self.assertIn('Raumplan', r.text)
        self.assertNotIn('<html', r.text)
        self.assertIn('Freie Räume am', student.get('/suche/', {'q': 'fr 14'}, headers={'X-Fragment': '1'}).text)
        self.assertIn('Köhler', self.login('asa').get('/suche/', {'q': 'KOEHLER'}, headers={'X-Fragment': '1'}).text)
        self.assertTrue(student.get('/datum/', {'q': 'nächsten fr 9'}).json()['iso'])

    def test_any_form_of_a_word_and_what_is_sought(self):
        groups = dict(found(Person.objects.get(username='student'), 'Studienpläne', 30))  # open to all, not only offices
        self.assertLessEqual({'Studiengänge', 'Mein Studienplan Violine'}, {h['title'] for h in groups['Funktionen']})
        self.assertEqual(len(groups['Studiengänge']), Program.objects.count())
        rooms = {h['title'] for h in dict(found(Person.objects.get(username='lehre'), 'Räume Orgel', 30))['Räume']}
        self.assertLessEqual(set(Room.objects.filter(purpose__icontains='orgel').values_list('name', flat=True)), rooms)
        self.assertLess(len(rooms), Room.objects.count())

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
        p = course.participations.exclude(scores__examiner=wendt).annotate(n=Count('scores')).filter(n__lt=course.staff.count() - 1).select_related('study').first()
        juror = self.login('lehre')  # a row others still mark: Wendt's own mark stays hers to change
        url = f'/aufnahme/pruefung/{course.pk}/sichtung/?bewerbung={p.pk}'
        juror.post(url, {f'score-{p.pk}-{wendt.pk}': '17,5'})
        juror.post(url, {f'score-{p.pk}-{wendt.pk}': '18'})
        juror.post(url, {f'score-{p.pk}-{wendt.pk}': '18'})
        marks = Change.objects.filter(table='courses.score', by=wendt, person=p.study.person)
        self.assertEqual([m.action for m in marks], ['changed', 'created'])
        self.assertEqual((marks[0].fields, marks[1].fields['value']), ({'value': ['17.50', '18']}, [None, '17.5']))  # the first mark is on record too

    def test_an_audition_s_history_is_for_the_office_that_runs_it(self):
        score = Score.objects.filter(participation__course__kind='audition', participation__course__released=None).select_related('participation__course').first()
        score.value -= 1
        score.save()
        audition, change = score.participation.course, Change.objects.get(table='courses.score', row=score.pk)
        for office in ('pruefungsamt', 'veranstaltungsbuero'):  # each sees and undoes changes elsewhere, never here
            client = self.login(office)
            refused = client.get(f'/verlauf/courses.course/{audition.pk}/')
            self.assertEqual(refused.status_code, 403)
            self.assertIn('Eignungsprüfung', refused.content.decode())  # says what it is and who keeps it
            self.assertEqual(client.post(f'/verlauf/{change.pk}/zuruecksetzen/').status_code, 403)
        self.assertIn(change, self.login('asa').get(f'/verlauf/courses.course/{audition.pk}/', headers=PANEL).context['changes'])

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
        '/raeume/', '/raeume/?von=10:00&personen=4', '/raeume/buchen/', '/raeume/meine/', '/raeume/veranstaltungen/', '/einstellungen/',
        '/einstellungen/texte/', '/einstellungen/semester/', '/einstellungen/semester/neu/', '/einstellungen/eignungspruefungen/neu/',
        '/auswertungen/', '/auswertungen/abfrage/', '/auswertungen/abfrage/?was=bewerbungen&nach=studienjahr', '/auswertungen/datensaetze/',
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


class DatesTest(SimpleTestCase):
    """Dates and times as people type them into every date field, on Friday 20 November 2026."""

    TODAY = date(2026, 11, 20)

    def test_days(self):
        for text, expected in [
            ('heute', '20.11.2026'), ('übermorgen', '22.11.2026'), ('+3', '23.11.2026'), ('-1', '19.11.2026'), ('in 2 wochen', '04.12.2026'),
            ('fr', '20.11.2026'), ('mo', '23.11.2026'), ('nächste woche', '23.11.2026'), ('nächsten fr', '27.11.2026'),
            ('12.11.', '12.11.2026'), ('12.01.', '12.01.2027'), ('25.', '25.11.2026'), ('5.', '05.12.2026'), ('12. dez', '12.12.2026'),
            ('3. okt 2027', '03.10.2027'), ('1.2.30', '01.02.2030'), ('1.2.85', '01.02.1985'), ('Do 22.10.2026', '22.10.2026'),
            ('2026-02-28', '28.02.2026'), ('31.2.', None), ('12', None), ('quatsch', None),
        ]:
            with self.subTest(text=text):
                found = dates.day(text, self.TODAY)
                self.assertEqual(found and f'{found:%d.%m.%Y}', expected)
        self.assertEqual(dates.day('5.', date(2026, 12, 20)), date(2027, 1, 5))

    def test_moments(self):
        for text, expected in [
            ('fr 14', ('20.11.2026', '14:00')), ('12.11. 9:30', ('12.11.2026', '09:30')), ('morgen um 10 uhr', ('21.11.2026', '10:00')),
            ('nächsten fr 9', ('27.11.2026', '09:00')), ('14:30', ('20.11.2026', '14:30')), ('12', ('20.11.2026', '12:00')),
            ('12.', ('12.12.2026', None)), ('quatsch', None),
        ]:
            with self.subTest(text=text):
                found = dates.moment(text, self.TODAY)
                self.assertEqual(found and (f'{found[0]:%d.%m.%Y}', found[1] and f'{found[1]:%H:%M}'), expected)


class KeysTest(Seeded):
    """Official keys change; what people told us stays, the statistics get today's key, and residents follow their town."""

    def test_territorial_changes(self):
        hanau = Person.objects.get(home_town='06415000')  # kreisfrei since 1.1.2026: moved along by the Gemeindeverzeichnis
        self.assertEqual(hanau.home, '06415')
        self.assertIn('Gebietsänderung zum 1.1.2026', Change.objects.filter(table='core.person', row=hanau.pk).exclude(reason='').first().reason)
        asa = self.login('asa')
        page = asa.get('/einstellungen/verzeichnisse/')
        self.assertEqual([p.home for p in page.context['people']], ['06435'])  # in the Main-Kinzig-Kreis, town unknown: to check
        eisenach = Person.objects.get(entrance_region='16056')  # kreisfrei until 30.6.2021, kept as given
        self.assertIn('heute Wartburgkreis', eisenach.get_entrance_region_display())
        from analytics.official import Report

        record = Report(Term.current()).record(eisenach)
        self.assertEqual((record['119U1'], record['119U2']), ('16', '063'))
        found = asa.get('/orte/?q=Kiliansroda').json()  # merged in 2026, still found for an HZB gained there
        self.assertEqual((found[0]['code'], found[0]['kreis']), ('16071038', '16071'))
        r = asa.post(f'/einstellungen/personen/{eisenach.pk}/', {**{f: getattr(eisenach, f) or '' for f in ['first_name', 'last_name', 'email', 'entrance', 'home']}, 'entrance_region': '16056'})
        self.assertNotIn('entrance_region', r.context['form'].errors if r.context else {})  # an ended key stays valid where a record holds it


class AccessibilityTest(Seeded):
    def test_a_barrier_reported_without_signing_in_reaches_the_office_with_its_deadline(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = Client().post('/barrierefreiheit/', {'email': 'gast@example.org', 'page': '/raeume/', 'text': 'Der Wochenplan liest sich mit dem Screenreader nicht.'}, follow=True)
        self.assertIn('Wir antworten bis zum', flashes(r)[0])
        self.assertEqual(mail.outbox[0].to, [Institution.get().email])
        self.assertContains(self.login('campus').get('/'), 'Barrierefreiheit: 1 Rückmeldung beantworten')


class AccountTest(Seeded):
    def test_an_applicant_changes_the_address_once_confirmed_from_it(self):
        jonas = Person.objects.get(username='bewerbung')
        self.login('bewerbung').post('/konto/', {'neue_adresse': '1', 'email': 'jonas.neu@example.org'})
        self.assertEqual((Person.objects.get(pk=jonas.pk).email, mail.outbox[-1].to), (jonas.email, ['jonas.neu@example.org']))  # not before confirming
        Client().get(re.search(r'/konto/adresse/\S+/', mail.outbox[-1].body)[0])
        self.assertEqual(Person.objects.get(pk=jonas.pk).email, 'jonas.neu@example.org')


class TransferTest(Seeded):
    def test_people_and_a_plan_come_back_as_they_left(self):
        """A Studiengang's students written out, deleted and loaded again read back as they were written, and so does its
        plan under a new PO-Version; a record that does not fit loads nothing and says where it is."""
        program = Program.objects.annotate(n=Count('studies')).filter(n__gte=5).order_by('n').first()
        names = list(Person.objects.filter(studies__program=program, groups=None, teaching=None, institute=None).values_list('username', flat=True).distinct())
        def plain(document):  # as it reads back from a file
            return json.loads(json.dumps(document, cls=DjangoJSONEncoder))

        written = plain(dump(Person.objects.filter(username__in=names), Program.objects.filter(pk=program.pk)))
        self.assertTrue(any(s.get('results') for p in written['people'] for s in p['studies']))
        Person.objects.filter(username__in=names).delete()
        load(written)
        self.assertEqual(plain(dump(Person.objects.filter(username__in=names), Program.objects.none()))['people'], written['people'])
        later = written['programs'][0] | {'version': program.version + 50}
        load({'format': FORMAT, 'programs': [later]})
        self.assertEqual(plain(dump(Person.objects.none(), Program.objects.filter(name=program.name, version=program.version + 50)))['programs'], [later])
        wrong = written['people'][0] | {'studies': [written['people'][0]['studies'][0] | {'program': ['X', 'Y', 1]}], 'last_name': 'Geändert'}
        with self.assertRaisesMessage(ValueError, 'people[0].studies[0]: Studiengang X, Y, 1 gibt es nicht'):
            load({'format': FORMAT, 'people': [wrong]})
        self.assertFalse(Person.objects.filter(last_name='Geändert').exists())

    def test_a_complete_copy_holds_every_table_but_no_password(self):
        """For a change of provider: every table, with what each field is, and no password."""
        out = BytesIO()
        rows = everything(out)
        copy = ZipFile(out)
        people = json.loads(copy.read('core.person.json'))
        self.assertEqual(len(people), Person.objects.count())
        self.assertNotIn('password', people[0])
        self.assertEqual(json.loads(copy.read('tables.json'))['core.person']['fields']['institute_id']['refers to'], 'core.institute')
        self.assertGreater(rows, len(people))


class SsoTest(Seeded):
    """Sign-in through the Hochschule's login: a signed answer, as a Shibboleth identity provider gives it."""

    def setUp(self):
        folder = Path(__file__).parent / 'testdata'
        self.key, cert = (folder / 'idp.key').read_text(), (folder / 'idp.crt').read_text()
        body = ''.join(line for line in cert.splitlines() if '-----' not in line)
        metadata = Path(tempfile.mkdtemp()) / 'idp.xml'
        metadata.write_text(
            '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="https://idp.test/idp">'
            '<IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol"><KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data>'
            f'<ds:X509Certificate>{body}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></KeyDescriptor>'
            '<SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.test/sso"/></IDPSSODescriptor></EntityDescriptor>',
        )
        self.cert = cert
        self.enterContext(self.settings(SAML=settings.SAML | {'idp': str(metadata)}, ALLOWED_HOSTS=['campus.test']))  # python3-saml wants a dotted host
        sso.provider.cache_clear()
        self.addCleanup(sso.provider.cache_clear)

    def answer(self, request, uid, mail):
        """The identity provider's signed answer to `request` (a redirect to it), vouching for `uid` with `mail`."""
        asked = re.search(rb'ID="([^"]+)"', Utils.decode_base64_and_inflate(parse_qs(urlsplit(request['Location']).query)['SAMLRequest'][0])).group(1).decode()
        now, at = Utils.now(), Utils.parse_time_to_SAML
        acs, sp, idp = 'http://campus.test/konto/sso/acs/', 'http://campus.test/konto/sso/metadata/', 'https://idp.test/idp'
        xml = (
            f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r{now}" '
            f'Version="2.0" IssueInstant="{at(now)}" Destination="{acs}" InResponseTo="{asked}"><saml:Issuer>{idp}</saml:Issuer>'
            '<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
            f'<saml:Assertion ID="_a{now}" Version="2.0" IssueInstant="{at(now)}"><saml:Issuer>{idp}</saml:Issuer>'
            '<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:transient">_n</saml:NameID>'
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData InResponseTo="{asked}" '
            f'NotOnOrAfter="{at(now + 300)}" Recipient="{acs}"/></saml:SubjectConfirmation></saml:Subject>'
            f'<saml:Conditions NotBefore="{at(now - 60)}" NotOnOrAfter="{at(now + 300)}"><saml:AudienceRestriction><saml:Audience>{sp}</saml:Audience>'
            f'</saml:AudienceRestriction></saml:Conditions><saml:AuthnStatement AuthnInstant="{at(now)}" SessionIndex="_s"><saml:AuthnContext>'
            '<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext>'
            '</saml:AuthnStatement><saml:AttributeStatement>'
            f'<saml:Attribute Name="{settings.SAML["id"]}"><saml:AttributeValue>{uid}</saml:AttributeValue></saml:Attribute>'
            f'<saml:Attribute Name="{settings.SAML["mail"]}"><saml:AttributeValue>{mail}</saml:AttributeValue></saml:Attribute>'
            '</saml:AttributeStatement></saml:Assertion></samlp:Response>'
        )
        return base64.b64encode(Utils.add_sign(xml, self.key, self.cert)).decode()

    def test_the_hochschule_vouches_for_whom_campus_knows(self):
        person = Person.objects.filter(studies__status='enrolled').first()
        client = Client(HTTP_HOST='campus.test')
        client.cookies['demo'] = ticket()
        self.assertIn('Mit dem Hochschulkonto anmelden', client.get('/konto/login/').text)
        asked = client.get('/konto/sso/', {'next': '/studierende/'})
        self.assertTrue(asked['Location'].startswith('https://idp.test/sso?SAMLRequest='))
        r = client.post('/konto/sso/acs/', {'SAMLResponse': self.answer(asked, 'k7@uni-weimar.de', person.email.upper()), 'RelayState': '/studierende/'})
        self.assertEqual((r['Location'], client.session['_auth_user_id']), ('/studierende/', str(person.pk)))
        self.assertEqual(Person.objects.get(pk=person.pk).sso_id, 'k7@uni-weimar.de')  # from now on known by it
        stranger = Client(HTTP_HOST='campus.test')
        stranger.cookies['demo'] = ticket()
        asked = stranger.get('/konto/sso/')
        stranger.post('/konto/sso/acs/', {'SAMLResponse': self.answer(asked, 'x9@uni-weimar.de', 'x9@uni-weimar.de')})
        self.assertNotIn('_auth_user_id', stranger.session)
        asked = stranger.get('/konto/sso/')  # an answer changed after signing, to someone ConCampus knows
        forged = base64.b64encode(base64.b64decode(self.answer(asked, 'x9@uni-weimar.de', 'x9@uni-weimar.de')).replace(b'x9@', b'k7@')).decode()
        stranger.post('/konto/sso/acs/', {'SAMLResponse': forged})
        self.assertNotIn('_auth_user_id', stranger.session)
        unasked = Client(HTTP_HOST='campus.test')  # a true answer, but to a request this browser did not make
        unasked.post('/konto/sso/acs/', {'SAMLResponse': self.answer(stranger.get('/konto/sso/'), 'k7@uni-weimar.de', '')})
        self.assertNotIn('_auth_user_id', unasked.session)
        self.assertIn('AssertionConsumerService', client.get('/konto/sso/metadata/').text)
