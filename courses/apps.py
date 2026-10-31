from collections import Counter

from django.apps import AppConfig
from django.db.models import Count, F, Q
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
            return 'Lehre'
        if 'student' in roles:
            return 'Lehrveranstaltungen'

    def figure(self, person):
        from core.models import Term
        from .models import Course
        from .views import TEACHING, deputat

        term = Term.current()
        if not term:
            return ''
        if 'manage_courses' in person.roles:
            return f'{person.within(term.courses.filter(kind__in=TEACHING), "manage_courses").count()} Veranstaltungen im {term}'
        if 'teacher' in person.roles:
            courses = person.teaching.filter(term=term, kind__in=TEACHING)
            return f'{plural(courses.count(), "Veranstaltung", "Veranstaltungen")} · {number_format(deputat(courses, person))} SWS'
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
        if roles & {'roll_over', 'publish_term'}:
            found.append(feature('Semesterwechsel', reverse('courses:rollover'), 'übernehmen nächstes semester planen kopieren'))
        return found

    def tasks(self, person):
        from django.utils import timezone

        from core.models import Term
        from students.models import Study
        from .models import Course, Participation
        from .views import TEACHING, registering, to_take, untimed

        found, term = [], Term.current()
        if 'teacher' in person.roles:
            waiting = Counter(untimed(Participation.objects.filter(course__staff=person)).values_list('course', flat=True))
            for course in Course.objects.filter(pk__in=waiting).order_by('title'):
                found.append(task(f'{course.title}: {plural(waiting[course.pk], "Person", "Personen")} ohne Unterrichtszeit', course.get_absolute_url()))
            choices, today = {}, timezone.localdate()
            advising = (  # the class's Schwerpunkt, chosen by the student after a Studienfachberatung; still open once its day has passed
                person.class_studies.filter(status=Study.Status.ENROLLED, focus=None).select_related('person', 'program', 'advised')
                .annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__term__start__lte=today)))
            )
            for s in advising:
                if (options := choices.setdefault(s.program_id, s.program.choices())) and s.fs >= (first := min(o.first or 99 for o in options)) - 1:
                    due, url = Study.choose_by(s.fs, first), f'{s.get_absolute_url()}#studienverlauf'
                    if s.advised_id is None:
                        found.append(task(f'Studienfachberatung: {s.person} ({options[0].choice.title})', url, due))
                    elif due < today:  # advised: the choice is the student's, who has the to-do
                        found.append(task(f'{options[0].choice.title}: empfohlen {s.advised.title}, wartet auf {s.person}', url))
            for course in person.teaching.filter(term=term, kind=TEACHING[0], bookings=None, cancelled=None).exclude(format__dated=False):
                found.append(task(f'Termine planen: {course.title}', course.get_absolute_url()))
            for planned in Term.objects.filter(planning=True, replies_by__isnull=False):  # the next semester: one's courses to confirm
                if n := person.teaching.filter(term=planned, kind__in=TEACHING, confirmed=None, cancelled=None, origin__isnull=False).count():
                    found.append(task(
                        f'{planned} planen: {plural(n, "Veranstaltung", "Veranstaltungen")} bestätigen oder absagen', f"{reverse('courses:mine')}?semester={planned.pk}", planned.replies_by,
                    ))
            for course in person.teaching.filter(term=term, signups__isnull=False).annotate(n=Count('signups')):
                found.append(task(f'{course.title}: {plural(course.n, "Anfrage", "Anfragen")} entscheiden', course.get_absolute_url()))
            for course in person.teaching.filter(term=term).annotate(n=Count('ensembles', filter=Q(ensembles__coach=None, ensembles__wish=person))).filter(n__gt=0):
                found.append(task(f'{course.title}: {plural(course.n, "Ensemble wünscht", "Ensembles wünschen")} Ihre Betreuung', course.get_absolute_url()))
            if term:
                from .views import taught

                if missing := [s.person for s in taught(person, term) if s.missing]:
                    found.append(task(f'Klasse: {plural(len(missing), "Person", "Personen")} ohne Einzelunterricht im {term}: {", ".join(p.last_name for p in missing)}', reverse('courses:mine')))
        if {'view_teaching_load', 'roll_over'} <= person.roles and term:  # who plans the teaching: whoever teaches more than they should
            from .views import loads

            if over := sum(1 for row in loads(term, person, institute=str(min(person.ours('view_teaching_load'))) if len(person.ours('view_teaching_load')) == 1 else '') if row.balance and row.balance > 0):
                found.append(task(f'{plural(over, "Lehrperson", "Lehrpersonen")} über der Lehrverpflichtung', f"{reverse('courses:load')}?stand=ueber"))
        if 'manage_courses' in person.roles and term:  # ensembles waiting for the office to give them a coach: an Institut's, where one keeps the course
            ensembles = person.within(term.courses.filter(allocation=Course.Allocation.ENSEMBLE), 'manage_courses')
            if person.everywhere('manage_courses'):
                from core.models import Office

                kept = Office.objects.filter(permissions__codename='manage_courses').exclude(institutes=None).values('institutes')
                ensembles = ensembles.exclude(Q(subject__institute__in=kept) | Q(staff__institute__in=kept))
            for course in ensembles.annotate(n=Count('ensembles', filter=Q(ensembles__coach=None))).filter(n__gt=0):
                found.append(task(f'{course.title}: {plural(course.n, "Ensemble", "Ensembles")} ohne Betreuung zuteilen', course.get_absolute_url()))
        if 'manage_courses' in person.roles and not person.everywhere('manage_courses'):  # an Institut's office
            if n := person.within(untimed(Participation.objects.all()), 'manage_courses').values('study').distinct().count():
                found.append(task(f'Einzelunterricht: {plural(n, "Person", "Personen")} ohne Unterrichtszeit', f"{reverse('students:list')}?stand=unterricht"))
            for planned in Term.objects.filter(planning=True, replies_by__isnull=False):  # the Institut answers for its courses too
                if n := person.within(planned.courses.filter(kind__in=TEACHING, confirmed=None, cancelled=None, origin__isnull=False), 'manage_courses').count():
                    found.append(task(
                        f'{planned} planen: {plural(n, "Veranstaltung", "Veranstaltungen")} noch ohne Antwort', f"{reverse('courses:catalogue')}?semester={planned.pk}&stand=offen", planned.replies_by,
                    ))
        if 'student' in person.roles and (coming := registering()):  # what the Studienplan wants and registration is open for
            for study in Study.objects.filter(person=person, status=Study.Status.ENROLLED).select_related('program', 'person'):
                items = to_take(study, coming)
                if any(c.is_open for _, there in items for c in there):
                    found.append(task(f'Anmeldung {coming}: {plural(len(items), "Modul", "Module")} noch ohne Veranstaltung', f"{reverse('courses:mine')}?semester={coming.pk}#offen", study.until))
        if 'roll_over' in person.roles:
            from .views import missing

            upcoming = Term.upcoming()
            if upcoming and (count := person.within(missing(upcoming), 'roll_over').exclude(rhythm=Course.Rhythm.ONCE).count()):
                found.append(task(f'Semesterwechsel: {plural(count, "Veranstaltung", "Veranstaltungen")} für {upcoming} übernehmen', reverse('courses:rollover'), upcoming.ready_by or upcoming.start))
        if 'publish_term' in person.roles and (upcoming := Term.upcoming()) and upcoming.planning and upcoming.replies_by and upcoming.courses.filter(kind__in=TEACHING).exclude(origin=None).exists():  # taken over and asked
            found.append(task(f'{upcoming} prüfen und veröffentlichen', reverse('courses:rollover'), upcoming.ready_by))
        return found
