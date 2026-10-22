from datetime import datetime, time, timedelta

from django.db.models import Q
from django.test import Client
from django.utils import timezone

from core import history
from core.models import Institute, Institution, Person
from core.testing import Seeded, flashes
from .models import Booking, BookingCategory, Room


def slot(hours, minutes=60):
    """A start `hours` from now on a quarter hour, moved to the next midnight if `minutes` would run past it."""
    start = (timezone.localtime() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    start += timedelta(minutes=-start.minute % 15)
    if (start + timedelta(minutes=minutes)).date() != start.date():
        start = timezone.make_aware(datetime.combine(start.date() + timedelta(days=1), time()))
    return start


def times(start, minutes=60):
    return {'day': f'{start:%d.%m.%Y}', 'begin': f'{start:%H:%M}', 'until': f'{start + timedelta(minutes=minutes):%H:%M}'}


class BookingRulesTest(Seeded):
    """The HfM's rules as Asimut has them: Institut rooms, 24 hours ahead, 4 hours a day, last-minute, chamber music."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mei = Person.objects.get(username='student')
        cls.own = Room.objects.get(name='FH 310')  # practice room of Mei's Institut
        with history.quietly():  # free rooms and an unused quota, whatever the time of day
            Booking.objects.filter(Q(room__name__in=['FH 310', 'FH 312', 'FH 324']) | Q(person=cls.mei)).delete()

    def setUp(self):
        self.student = self.login('student')

    def book(self, client=None, follow=False, **data):
        return (client or self.student).post('/raeume/buchen/', {'room': self.own.pk, 'category': 'uebung', **data}, follow=follow)

    def refused(self, response, text):
        self.assertEqual(response.status_code, 200)
        self.assertIn(text, response.context['form'].errors.as_text())

    def test_within_a_day_once(self):
        start = slot(5)
        self.assertEqual(flashes(self.book(**times(start), follow=True)), ['Buchung gespeichert.'])
        self.refused(self.book(**times(start)), 'bereits belegt')
        self.refused(self.book(**times(slot(30))), 'höchstens 24 Stunden')

    def test_four_hours_a_day_but_last_minute_is_free(self):
        start = slot(5, 300)
        self.book(**times(start))
        self.refused(self.book(**times(start + timedelta(hours=1), 240)), 'Kontingent von 4 Stunden')
        self.assertEqual(flashes(self.book(**times(slot(1, 45), 45), follow=True)), ['Buchung gespeichert.'])

    def test_rooms_and_categories_students_may_not_book(self):
        self.refused(self.book(room=Room.objects.get(name='Festsaal').pk, **times(slot(5))), 'gültige Auswahl')
        self.refused(self.book(room=Room.objects.get(name='HzH 003').pk, **times(slot(5))), 'gültige Auswahl')
        self.refused(self.book(category='konzert', **times(slot(5))), 'gültige Auswahl')

    def test_chamber_music_needs_a_partner_and_may_book_ten_days_ahead(self):
        chamber = {'room': Room.objects.get(name='FH 324').pk, 'category': 'kammermusik', 'title': 'Probe Quartett', **times(slot(72, 120), 120)}
        self.refused(self.book(**chamber), 'mindestens 1 weitere Person')
        partner = Person.objects.filter(studies__status='enrolled', studies__subject__name='Viola').first()
        self.assertEqual(flashes(self.book(**chamber, participants=[partner.pk], follow=True)), ['Buchung gespeichert.'])
        rehearsal = Booking.objects.get(title='Probe Quartett')
        self.assertEqual(list(rehearsal.participants.all()), [partner])
        self.assertIn(rehearsal, self.login(partner.username).get('/raeume/meine/').context['attending'])
        self.refused(self.book(**{**chamber, **times(slot(24 * 12))}, participants=[partner.pk]), 'höchstens 10 Tage')

    def test_teachers_book_series_half_a_year_ahead(self):
        teacher = self.login('lehre')
        lesson = {'room': Room.objects.get(name='FH 312').pk, 'category': 'unterricht', 'title': 'Zusatzunterricht'}
        self.book(teacher, **lesson, **times(slot(48, 50), 50), repeat_until='in 4 wochen', participants=[self.mei.pk])
        self.assertGreaterEqual(Booking.objects.filter(title='Zusatzunterricht').count(), 4)
        self.assertEqual(self.mei.attending.filter(title='Zusatzunterricht').count(), Booking.objects.filter(title='Zusatzunterricht').count())
        self.refused(self.book(teacher, **lesson, **times(slot(24 * 200))), '183 Tage')


class PlansTest(Seeded):
    def test_room_groups_and_privacy(self):
        student = self.login('student')
        self.assertEqual(student.get('/raeume/').context['group'], 'meine')
        piano = Institute.objects.get(name='Klavier')
        r = student.get('/raeume/', {'raeume': f'institut:{piano.pk}'})
        self.assertTrue(r.context['rooms'])
        self.assertIn('belegt', r.text)
        self.assertEqual(self.login('veranstaltungsbuero').get('/raeume/').context['group'], 'zentral')

    def test_events_search_and_cancellations(self):
        student = self.login('student')
        self.assertTrue(student.get('/raeume/veranstaltungen/').context['events'])
        r = student.get('/suche/', {'q': 'konzert'}, headers={'X-Fragment': '1'})
        self.assertIn('Termine', r.text)
        cancelled = Booking.objects.get(category='abgesagt')
        self.assertFalse(Booking(room=cancelled.room, start=cancelled.start, end=cancelled.end).clashes().exists())

    def test_calendar_feed(self):
        feed = self.login('student').get('/raeume/meine/').context['feed']
        r = Client().get(feed.replace('http://testserver', ''))
        self.assertEqual(r['Content-Type'], 'text/calendar; charset=utf-8')
        self.assertIn('BEGIN:VEVENT', r.text)
        self.assertEqual(Client().get('/raeume/kalender/falsch.ics').status_code, 404)


class RulesAreDataTest(BookingRulesTest):
    """What the Hochschule and its categories set is what the form enforces."""

    def test_the_hochschule_sets_the_horizon_and_the_quota(self):
        Institution.objects.update(student_ahead_hours=48, student_daily_hours=1)
        self.assertEqual(flashes(self.book(**times(slot(30)), follow=True)), ['Buchung gespeichert.'])
        self.refused(self.book(**times(slot(5), 120)), 'Kontingent von 1 Stunden')

    def test_a_category_sets_its_own_rules(self):
        BookingCategory.objects.filter(key='kammermusik').update(participants=2, ahead_hours=24)
        partner = Person.objects.filter(studies__status='enrolled', studies__subject__name='Viola').first()
        chamber = {'room': Room.objects.get(name='FH 324').pk, 'category': 'kammermusik'}
        self.refused(self.book(**chamber, **times(slot(5)), participants=[partner.pk]), 'mindestens 2 weitere Personen')
        self.refused(self.book(**chamber, **times(slot(30)), participants=[partner.pk]), 'höchstens 24 Stunden')
        BookingCategory.objects.filter(key='uebung').update(students=False)
        self.refused(self.book(**times(slot(5))), 'gültige Auswahl')
