from django.apps import AppConfig
from django.db.models import Count, Min, Q
from django.urls import reverse
from django.utils import timezone

from core.apps import Tile, feature, plural, task


class ExamsConfig(Tile, AppConfig):
    name = 'exams'
    verbose_name = 'Prüfungen'
    order = 3
    icon = 'M12 2a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM15.5 12.9 17 22l-5-3-5 3 1.5-9.1'
    url = 'exams:index'

    def title(self, roles):
        if roles & {'Prüfungsamt', 'teacher', 'student'}:
            return 'Prüfungen'

    def figure(self, person):
        from core.models import Term
        from courses.models import Course, Participation

        term = Term.current()
        if 'Prüfungsamt' in person.roles:
            return f'{plural(term.courses.filter(kind=Course.Kind.EXAM).count(), "Prüfung", "Prüfungen")} im {term}'
        if 'teacher' in person.roles:
            return f'{plural(person.teaching.filter(term=term, kind=Course.Kind.EXAM).count(), "Kommission", "Kommissionen")} im {term}'
        open_ = Participation.objects.filter(study__person=person, course__kind=Course.Kind.EXAM, course__released=None)
        return f'{plural(open_.count(), "Prüfung", "Prüfungen")} angemeldet'

    def features(self, person):
        roles, found = person.roles, []
        if roles & {'Prüfungsamt', 'teacher'}:
            found.append(feature('Prüfungen', reverse('exams:index'), 'prüfungsplan kommission noten eintragen bewerten freigabe'))
        if 'Prüfungsamt' in roles:
            found += [
                feature('Prüfung anlegen', reverse('exams:new'), 'neue prüfung erstellen'),
                feature('Anerkennung', reverse('exams:recognition'), 'anrechnung externe leistungen hochschulwechsel'),
            ]
        if 'student' in roles:
            found.append(feature('Prüfungsanmeldung', reverse('exams:index'), 'prüfungen anmelden abmelden meine prüfungen'))
            for study in person.studies.filter(status='enrolled').select_related('subject'):
                found.append(feature(f'Leistungsübersicht {study.subject}', reverse('exams:transcript', args=[study.pk]), 'noten ects transcript of records zeugnis notenspiegel'))
        return found

    def tasks(self, person):
        from courses.models import Course

        found, now = [], timezone.now()
        exams = Course.objects.filter(kind=Course.Kind.EXAM, released=None).annotate(
            held=Min('bookings__start'), size=Count('participations', distinct=True),
            open=Count('participations', filter=Q(participations__passed=None), distinct=True),
        ).filter(size__gt=0)
        if 'Prüfungsamt' in person.roles:
            for exam in exams.filter(open=0):
                found.append(task(f'Ergebnisse freigeben: {exam.title}', exam.get_absolute_url()))
        if 'teacher' in person.roles:
            for exam in exams.filter(staff=person, held__lte=now):
                missing = exam.participations.exclude(scores__examiner=person).count()
                if missing:
                    found.append(task(f'{exam.title}: Ihre Wertung fehlt für {plural(missing, "Teilnahme", "Teilnahmen")}', exam.get_absolute_url()))
        if 'student' in person.roles:
            from .views import offered

            if exams := list(offered(person)):
                due = min(e.deadline for e in exams if e.deadline)
                found.append(task(f'Prüfungsanmeldung: {plural(len(exams), "Prüfung", "Prüfungen")} möglich', reverse('exams:index'), due))
        return found
