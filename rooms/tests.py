from collections import defaultdict
from datetime import datetime, time, timedelta
from html.parser import HTMLParser

from django.db.models import Q
from django.test import Client
from django.utils import timezone

from core import history
from core.models import Institute, Institution, Person
from core.testing import Seeded, flashes
from courses.models import Ensemble
from .models import Booking, BookingCategory, Room
from .views import agenda


def slot(hours, minutes=60):
    """A start `hours` from now on a quarter hour, moved to the next midnight if `minutes` would run past it."""
    start = (timezone.localtime() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    start += timedelta(minutes=-start.minute % 15)
    if (start + timedelta(minutes=minutes)).date() != start.date():
        start = timezone.make_aware(datetime.combine(start.date() + timedelta(days=1), time()))
    return start


def times(start, minutes=60):
    return {'day': f'{start:%d.%m.%Y}', 'begin': f'{start:%H:%M}', 'until': f'{start + timedelta(minutes=minutes):%H:%M}'}


def sent(response, **changed):
    """What a browser sends of the form in `response`: its inputs and the options selected, with `changed`."""
    data, select = defaultdict(list), None

    class Reader(HTMLParser):
        def handle_starttag(self, tag, attrs):
            nonlocal select
            found = dict(attrs)
            if tag == 'input' and 'name' in found:
                data[found['name']].append(found.get('value') or '')
            elif tag == 'select':
                select = found['name']
            elif tag == 'option' and 'selected' in found:
                data[select].append(found['value'])

    Reader().feed(response.content.decode())
    return {**data, **changed}


class TeacherTest(Seeded):
    """What teachers book beyond their Institut's rooms, and calling a day off."""

    def test_a_hall_is_asked_for_and_given_by_the_office(self):
        hall, start = Room.objects.get(name='Festsaal'), slot(24 * 40, 120)
        self.assertTrue(hall.function.central)
        self.login('lehre').post('/raeume/buchen/', {'room': hall.pk, 'category': 'klassenabend', 'title': 'Klassenabend', 'audience': 60, **times(start, 120)})
        asked = Booking.objects.get(room=hall, start=start, title='Klassenabend')
        self.assertTrue(asked.requested)
        self.assertFalse(Booking(room=hall, start=start, end=start + timedelta(hours=1), category_id='konzert').clashes().exists())  # it holds nothing yet
        self.login('veranstaltungsbuero').post(f'/raeume/anfragen/{asked.pk}/', {'decision': 'yes'})
        self.assertFalse(Booking.objects.get(pk=asked.pk).requested)
        self.assertTrue(asked.person.messages.filter(subject__startswith='Bestätigt').exists())

    def test_a_day_called_off_tells_each_student_once(self):
        wendt = Person.objects.get(username='lehre')
        day = next(b.start for b in Booking.objects.filter(person=wendt, course__kind='lesson', start__gt=timezone.now() + timedelta(days=1), category__blocks=True).order_by('start'))
        lessons = Booking.objects.filter(person=wendt, start__date=timezone.localdate(day), category__blocks=True)
        students = set(Person.objects.filter(attending__in=lessons))
        self.login('lehre').post(f'/raeume/tag-absagen/?tag={timezone.localdate(day):%Y-%m-%d}', {'booking': [b.pk for b in lessons]})
        self.assertFalse(Booking.objects.filter(pk__in=lessons, category__blocks=True).exists())
        self.assertEqual({p: p.messages.filter(subject__startswith='Abgesagt: Ihre Termine').count() for p in students}, dict.fromkeys(students, 1))

    def test_a_student_reports_missing_a_lesson_and_the_teacher_hears(self):
        mei, wendt = Person.objects.get(username='student'), Person.objects.get(username='lehre')
        lesson = Booking.objects.filter(participants=mei, person=wendt, start__gt=timezone.now(), category__blocks=True).first()
        self.login('student').post(f'/raeume/buchung/{lesson.pk}/abmelden/', {'note': 'Probe im Orchester'})
        self.assertTrue(lesson.absent.filter(pk=mei.pk).exists())
        self.assertTrue(wendt.messages.filter(subject__startswith='Abgemeldet', body__contains='Probe im Orchester').exists())

    def test_teachers_see_that_a_student_practises_not_who(self):
        practice = Booking.objects.filter(category__tone='practice', person__studies__status='enrolled', end__gt=timezone.now()).first()
        self.assertEqual(self.login('lehre').get(f'/raeume/buchung/{practice.pk}/').status_code, 403)

    def test_rooms_and_admissions_say_whom_they_are_for(self):
        self.assertEqual(self.login('bewerbung').get('/raeume/').status_code, 403)
        refused = self.login('pruefungsamt').get('/aufnahme/')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('für Bewerbende', refused.content.decode())

    def test_participants_are_found_by_name_not_listed(self):
        teacher = self.login('lehre')
        listed = teacher.get('/raeume/buchen/').context['form'].fields['participants']
        self.assertLess(len(list(listed.choices)), 40)  # the class and their ensembles, not every student
        self.assertEqual(teacher.get('/studierende/vorschlaege/', {'q': 'ma'}).json()['found'], [])
        self.assertLessEqual(len(teacher.get('/studierende/vorschlaege/', {'q': 'mar'}).json()['found']), 8)


class BookingRulesTest(Seeded):
    """The HfM's rules as Asimut has them: Institut rooms, 24 hours ahead, 4 hours a day, last-minute, chamber music; what the
    Hochschule and its categories set is what the form enforces."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mei = Person.objects.get(username='student')
        cls.own = Room.objects.get(name='FH 310')  # practice room of Mei's Institut
        with history.quietly():  # free rooms, an unused quota and a free timetable, whatever the time of day
            Booking.objects.filter(
                Q(room__name__in=['FH 310', 'FH 312', 'FH 324']) | Q(person=cls.mei) | Q(participants=cls.mei) | Q(course__participations__study__person=cls.mei)
            ).delete()

    def setUp(self):
        self.student = self.login('student')

    def book(self, client=None, follow=False, **data):
        return (client or self.student).post('/raeume/buchen/', {'room': self.own.pk, 'category': 'uebung', **data}, follow=follow)

    def refused(self, response, text):
        self.assertEqual(response.status_code, 200)
        self.assertIn(text, response.context['form'].errors.as_text())

    def test_a_rehearsal_is_the_ensemble_s(self):
        ensemble = Ensemble.objects.filter(members__person=self.mei).first()
        start = slot(26)
        self.book(category='kammermusik', ensemble=ensemble.pk, **times(start))
        rehearsal = Booking.objects.get(person=self.mei, start=start)
        self.assertEqual((rehearsal.course, set(rehearsal.participants.all())), (ensemble.course, {s.person for s in ensemble.members.all()} - {self.mei}))
        other = Person.objects.filter(studies__participations__course=ensemble.course).exclude(studies__ensembles=ensemble).first()
        self.assertFalse(agenda(other).filter(pk=rehearsal.pk).exists())  # the course's other students see none of it

    def test_within_a_day_once(self):
        start = slot(5)
        self.assertEqual(flashes(self.book(**times(start), follow=True)), ['Buchung gespeichert.'])
        self.refused(self.book(**times(start)), 'bereits belegt')
        self.refused(self.book(**times(slot(30))), 'höchstens 24 Stunden')

    def test_a_taken_room_offers_another_or_a_later_time_in_one_click(self):
        start = slot(5, 120)
        other_student = Person.objects.filter(studies__status='enrolled').exclude(pk=self.mei.pk).first()
        Booking.objects.create(room=self.own, start=start, end=start + timedelta(hours=1), person=other_student, category_id='uebung')
        instead = self.book(**times(start)).context['instead']
        self.assertEqual(instead['later'][0], start + timedelta(hours=1))
        other = instead['rooms'][0]
        self.assertEqual(flashes(self.book(**times(start), instead=f'raum:{other.pk}', follow=True)), ['Buchung gespeichert.'])
        self.assertTrue(Booking.objects.filter(room=other, start=start, person=self.mei).exists())

    def test_not_in_two_places_at_once(self):
        start = slot(5)
        lesson = Booking.objects.create(room=Room.objects.get(name='FH 312'), start=start, end=start + timedelta(hours=1), person=Person.objects.get(username='lehre'), category_id='unterricht')
        lesson.participants.set([self.mei])
        self.refused(self.book(**times(start)), 'Sie haben zu dieser Zeit schon')

    def test_four_hours_a_day_but_last_minute_is_free(self):
        start = slot(5, 300)
        self.book(**times(start))
        self.refused(self.book(**times(start + timedelta(hours=1), 240)), 'höchstens 4 Stunden am Tag')
        self.assertEqual(flashes(self.book(**times(slot(1, 45), 45), follow=True)), ['Buchung gespeichert. Sie beginnt in weniger als 3 Stunden und zählt deshalb nicht zu Ihren 4 Stunden am Tag.'])

    def test_found_booked_and_moved_beside_the_plan(self):
        """The plan's filter finds the room; beyond the 24 hours of practice, a click books a rehearsal of Mei's ensemble,
        which moves later without losing who comes."""
        start, room, quick = slot(26, 120), Room.objects.get(name='FH 324'), {'X-Quick': '1', 'X-Panel': '1'}
        rooms = self.student.get('/raeume/', {'tag': f'{start:%Y-%m-%d}', 'von': f'{start:%H:%M}', 'personen': 3}).context['rooms']
        self.assertIn(room, rooms)
        self.assertTrue(all(r.capacity >= 3 for r in rooms))
        asked = self.student.get('/raeume/buchen/', {'raum': room.pk, 'start': f'{start:%Y-%m-%dT%H:%M}', 'ende': f'{start + timedelta(hours=2):%Y-%m-%dT%H:%M}'}, headers=quick)
        self.assertEqual(self.student.post('/raeume/buchen/', sent(asked), headers=quick).status_code, 204)
        rehearsal = Booking.objects.get(person=self.mei, room=room, start=start, category='kammermusik')
        members = set(rehearsal.participants.all())
        self.assertTrue(members)
        moving = self.student.get(f'/raeume/buchung/{rehearsal.pk}/', headers=quick)
        self.student.post(f'/raeume/buchung/{rehearsal.pk}/', sent(moving, until=f'{start + timedelta(hours=1):%H:%M}'), headers=quick)
        rehearsal.refresh_from_db()
        self.assertEqual((rehearsal.end, set(rehearsal.participants.all())), (start + timedelta(hours=1), members))

    def test_rooms_and_categories_students_may_not_book(self):
        self.refused(self.book(room=Room.objects.get(name='Festsaal').pk, **times(slot(5))), 'gültige Auswahl')
        self.refused(self.book(room=Room.objects.get(name='HzH 003').pk, **times(slot(5))), 'gültige Auswahl')
        self.refused(self.book(category='konzert', **times(slot(5))), 'gültige Auswahl')

    def test_chamber_music_needs_a_partner_and_may_book_ten_days_ahead(self):
        chamber = {'room': Room.objects.get(name='FH 324').pk, 'category': 'kammermusik', 'title': 'Probe Quartett', **times(slot(72, 120), 120)}
        self.refused(self.book(**chamber), 'mindestens 1 weitere Person')
        partner = Person.objects.filter(studies__status='enrolled', studies__subject__name='Viola').first()
        self.assertTrue(flashes(self.book(**chamber, participants=[partner.pk], follow=True))[0].startswith('Buchung gespeichert.'))
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

    def test_the_hochschule_sets_the_horizon_and_the_quota(self):
        Institution.objects.update(student_ahead_hours=48, student_daily_hours=1)
        self.assertEqual(flashes(self.book(**times(slot(30)), follow=True)), ['Buchung gespeichert.'])
        self.refused(self.book(**times(slot(5, 120), 120)), 'höchstens 1 Stunde am Tag')

    def test_a_category_sets_its_own_rules(self):
        BookingCategory.objects.filter(key='kammermusik').update(participants=2, ahead_hours=24)
        partner = Person.objects.filter(studies__status='enrolled', studies__subject__name='Viola').first()
        chamber = {'room': Room.objects.get(name='FH 324').pk, 'category': 'kammermusik'}
        self.refused(self.book(**chamber, **times(slot(5)), participants=[partner.pk]), 'mindestens 2 weitere Personen')
        self.refused(self.book(**chamber, **times(slot(30)), participants=[partner.pk]), 'höchstens 24 Stunden')
        BookingCategory.objects.filter(key='uebung').update(students=False)
        self.refused(self.book(**times(slot(5))), 'gültige Auswahl')


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
