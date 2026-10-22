from django.apps import AppConfig
from django.utils.formats import number_format

from django.urls import reverse

from core.apps import Tile, feature, plural, task

class CoursesConfig(Tile, AppConfig):
    name = 'courses'
    verbose_name = 'Lehre'
    order = 2
    icon = 'M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2zM22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z'
    url = 'courses:index'

    def title(self, roles):
        if 'manage_courses' in roles:
            return 'Lehre'
        if 'teacher' in roles:
            return 'Meine Lehre'
        if 'student' in roles:
            return 'Lehrveranstaltungen'

    def figure(self, person):
        from core.models import Term
        from .models import Course
        from .views import TEACHING, deputat

        term = Term.current()
        if 'manage_courses' in person.roles:
            return f'{term.courses.filter(kind__in=TEACHING).count()} Veranstaltungen im {term}'
        if 'teacher' in person.roles:
            courses = person.teaching.filter(term=term, kind__in=TEACHING)
            return f'{plural(courses.count(), "Veranstaltung", "Veranstaltungen")} · {number_format(deputat(courses))} SWS'
        count = Course.objects.filter(term=term, kind__in=TEACHING, participations__study__person=person).distinct().count()
        return f'{count} belegt im {term}'

    def features(self, person):
        roles = person.roles
        found = [feature('Vorlesungsverzeichnis', reverse('courses:catalogue'), 'veranstaltungen lehrangebot kurse seminare anmelden belegen')]
        if roles & {'teacher', 'student'}:
            found += [
                feature('Meine Lehre' if 'teacher' in roles else 'Meine Veranstaltungen', reverse('courses:mine'), 'belegt angemeldet warteliste klasse'),
                feature('Stundenplan', reverse('courses:timetable'), 'woche kalender termine'),
            ]
        if 'manage_courses' in roles:
            found.append(feature('Neue Veranstaltung', reverse('courses:new'), 'anlegen erstellen kurs'))
        if roles & {'view_teaching_load', 'teacher'}:
            found.append(feature('Deputat', reverse('courses:load'), 'lehrverpflichtung sws lehrende auslastung'))
        if 'roll_over' in roles:
            found.append(feature('Semesterwechsel', reverse('courses:rollover'), 'übernehmen nächstes semester planen kopieren'))
        return found

    def tasks(self, person):
        from core.models import Term
        from .views import TEACHING

        found, term = [], Term.current()
        if 'teacher' in person.roles:
            for course in person.teaching.filter(term=term, kind=TEACHING[0], bookings=None):
                found.append(task(f'Termine planen: {course.title}', course.get_absolute_url()))
        if 'roll_over' in person.roles:
            from .views import missing

            upcoming = Term.upcoming()
            if count := missing(upcoming).count():
                found.append(task(f'Semesterwechsel: {plural(count, "Veranstaltung", "Veranstaltungen")} für {upcoming} übernehmen', reverse('courses:rollover'), upcoming.start))
        return found
