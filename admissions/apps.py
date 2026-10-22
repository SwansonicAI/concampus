from django.apps import AppConfig

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.apps import Tile, feature, plural, task


class AdmissionsConfig(Tile, AppConfig):
    name = 'admissions'
    verbose_name = 'Aufnahme'
    order = 4
    icon = 'M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z'
    url = 'admissions:index'

    def title(self, roles):
        if roles & {'view_applications', 'teacher'}:
            return 'Aufnahme'
        if 'applicant' in roles or not roles:
            return 'Meine Bewerbung'

    def figure(self, person):
        from courses.models import Course
        from students.models import Study

        if 'view_applications' in person.roles:
            return f'{Study.objects.filter(status=Study.Status.APPLIED).count()} offene Bewerbungen'
        if 'teacher' in person.roles:
            juries = person.teaching.filter(kind=Course.Kind.AUDITION, released=None).count()
            return f'{plural(juries, "Eignungsprüfung", "Eignungsprüfungen")} als Kommission'
        latest = person.studies.filter(status__in=Study.APPLICATION).first()
        return latest.get_status_display() if latest else 'Jetzt bewerben'

    def features(self, person):
        roles, found = person.roles, []
        if 'view_applications' in roles:
            found.append(feature('Bewerbungen', reverse('admissions:board'), 'eignungsprüfung aufnahmeprüfung zulassung bewerber entscheiden'))
        if 'teacher' in roles:
            found.append(feature('Meine Kommissionen', reverse('admissions:juries'), 'eignungsprüfung aufnahmeprüfung jury bewerten'))
        if 'applicant' in roles or not roles:
            found += [
                feature('Meine Bewerbung', reverse('admissions:mine'), 'status einladung termin ergebnis video unterlagen hochladen'),
                feature('Neue Bewerbung', reverse('admissions:apply'), 'bewerben studiengang'),
            ]
        elif 'student' in roles:
            found.append(feature('Bewerbung für ein weiteres Studium', reverse('admissions:apply'), 'master konzertexamen bewerben eignungsprüfung'))
        return found

    def tasks(self, person):
        from courses.models import Course
        from students.models import Study

        found = []
        if 'teacher' in person.roles:
            open_ = person.teaching.filter(kind=Course.Kind.AUDITION, released=None)
            for course in open_.exclude(participations=None).distinct():
                missing = course.participations.exclude(scores__examiner=person).count()
                started = course.video or course.participations.filter(slot__lte=timezone.now()).exists()
                if missing and started:
                    url = reverse('admissions:review', args=[course.pk]) if course.video else course.get_absolute_url()
                    found.append(task(f'{course.title}: Ihre Wertung fehlt für {plural(missing, "Bewerbung", "Bewerbungen")}', url, course.deadline))
        if 'decide_applications' in person.roles:
            ready = [
                s for s in Study.objects.filter(status=Study.Status.APPLIED).prefetch_related('participations__course')
                if (stages := [p for p in s.participations.all() if p.course.kind == Course.Kind.AUDITION])
                and all(p.passed is not None for p in stages)
                and (any(p.passed is False for p in stages) or any(p.course.stage == 'Hauptfach' for p in stages))
            ]
            if ready:
                found.append(task(f'{plural(len(ready), "Bewerbung", "Bewerbungen")} entscheiden', reverse('admissions:board')))
        from .views import incomplete, uploads_open

        for study in person.studies.filter(status=Study.Status.APPLIED).select_related('person', 'program', 'subject').prefetch_related('documents'):
            if uploads_open(study) and (missing := incomplete(study)):
                deadline = study.participations.filter(course__program__isnull=False).values_list('course__deadline', flat=True).first()
                found.append(task(f'Für die Bewerbung {study.subject} fehlt noch: {", ".join(missing)}', reverse('admissions:mine'), deadline))
        for study in person.studies.filter(status=Study.Status.ADMITTED, terms=None):
            found.append(task(f'Studienplatz {study.subject} annehmen oder ablehnen', reverse('admissions:mine'), study.since + timedelta(days=14)))
        for p in person.studies.filter(status=Study.Status.APPLIED).values_list('participations__course__title', 'participations__slot'):
            if p[1] and p[1] > timezone.now():
                found.append(task(f'{p[0]} am {timezone.localtime(p[1]):%d.%m. um %H:%M} Uhr', reverse('admissions:mine'), timezone.localdate(p[1])))
        return found
