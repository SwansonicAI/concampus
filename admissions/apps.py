from collections import Counter

from django.apps import AppConfig
from django.db.models import Prefetch
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
        if roles & {'view_applications', 'juror'}:
            return 'Aufnahme'
        if 'applicant' in roles or not roles:
            return 'Meine Bewerbung'

    def figure(self, person):
        from courses.models import Course
        from students.models import Study

        if 'view_applications' in person.roles:
            return f'{person.within(Study.objects.filter(status=Study.Status.APPLIED), "view_applications").count()} offene Bewerbungen'
        if 'juror' in person.roles:
            juries = person.teaching.filter(kind=Course.Kind.AUDITION, released=None).count()
            return f'{plural(juries, "Eignungsprüfung", "Eignungsprüfungen")} als Kommission'
        latest = person.studies.filter(status__in=Study.APPLICATION).first()
        return latest.get_status_display() if latest else 'Jetzt bewerben'

    def features(self, person):
        roles, found = person.roles, []
        if 'view_applications' in roles:
            found.append(feature('Bewerbungen', reverse('admissions:board'), 'eignungsprüfung aufnahmeprüfung zulassung bewerber entscheiden'))
        if 'juror' in roles:
            found.append(feature('Meine Kommissionen', reverse('admissions:juries'), 'eignungsprüfung aufnahmeprüfung jury bewerten'))
        if 'applicant' in roles or not roles:
            found += [
                feature('Meine Bewerbung', reverse('admissions:mine'), 'status einladung termin ergebnis video unterlagen hochladen'),
                feature('Neue Bewerbung', reverse('admissions:apply'), 'bewerben studiengang'),
            ]
        elif roles & {'student', 'alumni'}:
            found.append(feature('Bewerbung für ein weiteres Studium', reverse('admissions:apply'), 'master konzertexamen bewerben eignungsprüfung'))
        return found

    def tasks(self, person):
        from core.models import Institution, Term
        from courses.models import Course
        from students.models import Study

        found = []
        if 'juror' in person.roles:
            open_ = person.teaching.filter(kind=Course.Kind.AUDITION, released=None)
            for course in open_.exclude(participations=None).distinct():
                missing = course.participations.exclude(scores__examiner=person).count()
                started = course.video or course.participations.filter(slot__lte=timezone.now()).exists()
                if missing and started:
                    url = reverse('admissions:review', args=[course.pk]) if course.video else course.get_absolute_url()
                    found.append(task(f'{course.title}: Ihre Wertung fehlt für {plural(missing, "Bewerbung", "Bewerbungen")}', url, course.deadline))
        from .views import accept_by, document_types, incomplete, stages, uploads_open, vacancies, verdict

        if 'decide_applications' in person.roles:
            ready = Counter(
                s.start_term for s in person.within(Study.objects.filter(status=Study.Status.APPLIED), 'decide_applications')
                .select_related('start_term').prefetch_related(Prefetch('participations', stages(), to_attr='stages')) if verdict(s.stages)
            )
            for term, n in sorted(ready.items(), key=lambda item: item[0].start):
                found.append(task(f'{plural(n, "Bewerbung", "Bewerbungen")} zum {term} entscheiden', f"{reverse('admissions:board')}?semester={term.pk}#entscheiden"))

        if 'decide_applications' in person.roles:
            for term in filter(None, [Term.current(), Term.upcoming()]):
                if moves := vacancies(term, person):
                    found.append(task(f'Nachrücken zum {term}: {plural(len(moves), "Studiengang", "Studiengänge")}', f"{reverse('admissions:board')}?semester={term.pk}"))
        types = document_types()
        for study in person.studies.filter(status=Study.Status.APPLIED).select_related('person', 'program', 'subject').prefetch_related('documents'):
            if uploads_open(study) and (missing := incomplete(study, types)):
                deadline = study.participations.filter(course__program__isnull=False).values_list('course__deadline', flat=True).first()
                found.append(task(f'Für die Bewerbung {study.subject} fehlt noch: {", ".join(missing)}', reverse('admissions:mine'), deadline))
        days = Institution.get().accept_days
        for study in person.studies.filter(status=Study.Status.ADMITTED, terms=None):
            if accept_by(study, days) >= timezone.localdate():
                found.append(task(f'Studienplatz {study.subject} annehmen oder ablehnen', reverse('admissions:mine'), accept_by(study, days)))
        for p in person.studies.filter(status=Study.Status.APPLIED).values_list('participations__course__title', 'participations__slot'):
            if p[1] and p[1] > timezone.now():
                found.append(task(f'{p[0]} am {timezone.localtime(p[1]):%d.%m. um %H:%M} Uhr', reverse('admissions:mine'), timezone.localdate(p[1])))
        return found
