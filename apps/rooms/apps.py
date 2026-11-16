from datetime import timedelta

from django.apps import AppConfig
from django.urls import reverse
from django.utils.formats import number_format

from core.apps import Tile, feature, page, plural, task


def place(poll):
    """Where a Terminumfrage's task belongs on the start page: with its audition or exam, else under Räume."""
    if poll.course is None:
        return {}
    return {'app': 'Bewerbungen' if poll.course.kind == poll.course.Kind.AUDITION else 'Prüfungen'}


class RoomsConfig(Tile, AppConfig):
    name = 'rooms'
    verbose_name = 'Räume'
    order = 5
    icon = 'M3 4h18v18H3zM16 2v4M8 2v4M3 10h18'
    url = 'rooms:index'

    def title(self, roles):
        if roles - {'applicant', 'alumni'}:
            return 'Räume'

    def pages(self, person):
        from .polls import may_ask

        found = [page('Raumplan', 'rooms:index', 'room', 'new'), page('Meine Buchungen', 'rooms:mine', 'booking', 'day_off'), page('Veranstaltungen', 'rooms:events')]
        found += [page('Terminumfragen', 'rooms:polls', 'new_poll', 'poll', 'choose')] if may_ask(person) else []
        return found + ([page('Anfragen', 'rooms:requests')] if 'manage_bookings' in person.roles else [])

    def features(self, person):
        from .polls import may_ask

        if not self.title(person.roles):
            return []
        found = [
            feature('Raumplan', reverse('rooms:index'), 'belegung räume heute kalender tagesplan institut gebäude raum buchen frei freier finden suchen verfügbar buchung reservieren üben überaum kammermusik probe ausstattung'),
            feature('Raum buchen', reverse('rooms:new'), 'überaum übezeit probe'),
            feature('Meine Buchungen', reverse('rooms:mine'), 'stornieren übezeit kontingent'),
            feature('Termine eines Tages absagen', reverse('rooms:day_off'), 'krank unterricht entfällt schließen'),
            feature('Veranstaltungen', reverse('rooms:events'), 'konzert klassenabend meisterkurs vortrag programm'),
            feature('Kalender abonnieren', f"{reverse('rooms:mine')}#kalender", 'ical ics outlook google apple handy feed'),
        ]
        if may_ask(person):
            found.append(feature('Terminumfragen', reverse('rooms:polls'), 'termin finden umfrage doodle sitzung besprechung treffen kommission wann abstimmen'))
        if 'manage_bookings' in person.roles:
            found += [
                feature('Veranstaltung anlegen', f"{reverse('rooms:new')}?veranstaltung", 'konzert klassenabend'),
                feature('Raumanfragen', reverse('rooms:requests'), 'entscheiden'),
            ]
        return found

    def figure(self, person):
        from django.utils import timezone

        from core.dates import starting
        from core.models import Institution
        from .forms import quota
        from .models import Booking
        from .plans import agenda

        today = timezone.localdate()
        if 'manage_bookings' in person.roles:
            return f'{person.within(Booking.objects.filter(**starting(today)), "manage_bookings").count()} Buchungen heute'
        if 'student' in person.roles:
            daily = Institution.get().student_daily_hours
            return f'Übezeit heute: noch {number_format(daily - quota(person, today))} von {daily} Std.'
        return f'Ihre Termine heute: {agenda(person).filter(**starting(today)).count()}'

    def tasks(self, person):
        from django.utils import timezone

        from django.db.models import Count, Q

        from .models import Booking, Poll

        polls = Poll.objects.filter(chosen=None).select_related('course')
        asked = polls.filter(people=person).annotate(times=Count('options', distinct=True), mine=Count('options__answers', filter=Q(options__answers__person=person), distinct=True))
        found = [  # asked and not every time answered; one's own, all answered or past the day; where its exam or audition is
            {**task(f'Wann können Sie? {p.title}' + (f' (noch {p.times - p.mine} Zeiten)' if p.mine else ''), p.get_absolute_url(), p.by), **place(p)}
            for p in asked if p.mine < p.times
        ] + [
            {**task(f'Termin wählen: {p.title}', p.get_absolute_url(), p.by), **place(p)}
            for p in polls.filter(person=person).annotate(answered=Count('options__answers__person', distinct=True), size=Count('people', distinct=True))
            if p.answered >= p.size or p.by < timezone.localdate()
        ]
        if 'manage_bookings' not in person.roles:
            return found
        soon = Booking.objects.filter(category__public=True, internal=False, requested=False, programme='', start__gte=timezone.now(), start__lte=timezone.now() + timedelta(days=14))
        found += [
            task(f'Programm fehlt: {b.label}', reverse('rooms:booking', args=[b.pk]), timezone.localdate(b.start))
            for b in person.within(soon, 'manage_bookings').select_related('category', 'course')
        ]
        asked = person.within(Booking.objects.filter(requested=True, end__gte=timezone.now()), 'manage_bookings').order_by('start')
        if first := asked.first():
            found.append(task(f'{plural(asked.count(), "Raumanfrage", "Raumanfragen")} entscheiden', reverse('rooms:requests'), timezone.localdate(first.start)))
        return found
