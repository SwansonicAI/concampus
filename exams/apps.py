from datetime import timedelta

from django.apps import AppConfig
from django.db.models import Count, Exists, F, Min, OuterRef, Q
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
        if roles & {'manage_exams', 'decide_results', 'recognise', 'teacher', 'student'}:
            return 'Prüfungen'

    def figure(self, person):
        from core.models import Term
        from courses.models import Course, Participation

        term = Term.current()
        if not term:
            return ''
        if person.roles & {'manage_exams', 'decide_results'}:
            exams = person.within(term.courses.filter(kind=Course.Kind.EXAM), 'manage_exams', 'decide_results')
            return f'{plural(exams.count(), "Prüfung", "Prüfungen")} im {term}'
        if 'teacher' in person.roles:
            return f'{plural(person.teaching.filter(term=term, kind=Course.Kind.EXAM).count(), "Kommission", "Kommissionen")} im {term}'
        open_ = Participation.objects.filter(study__person=person, course__kind=Course.Kind.EXAM, course__released=None)
        return f'{plural(open_.count(), "Prüfung", "Prüfungen")} angemeldet'

    def features(self, person):
        roles, found = person.roles, []
        if roles & {'manage_exams', 'decide_results', 'teacher'}:
            found.append(feature('Prüfungen', reverse('exams:index'), 'prüfungsplan kommission noten eintragen bewerten freigabe'))
        if 'manage_exams' in roles:
            found.append(feature('Prüfung anlegen', reverse('exams:new'), 'neue prüfung erstellen'))
        if 'recognise' in roles:
            found.append(feature('Anerkennung', reverse('exams:recognition'), 'anrechnung externe leistungen hochschulwechsel'))
        if 'student' in roles:
            found.append(feature('Prüfungsanmeldung', reverse('exams:index'), 'prüfungen anmelden abmelden meine prüfungen'))
            for study in person.studies.filter(status='enrolled').select_related('subject'):
                found.append(feature(f'Leistungsübersicht {study.subject}', reverse('exams:transcript', args=[study.pk]), 'noten ects transcript of records zeugnis notenspiegel'))
        for study in person.studies.filter(status='graduated').select_related('subject'):
            found.append(feature(f'Zeugnis {study.subject}', reverse('exams:transcript', args=[study.pk]), 'abschluss noten transcript of records diploma supplement'))
        return found

    def tasks(self, person):
        from courses.models import Course

        found, now = [], timezone.now()
        exams = Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], released=None).annotate(
            held=Min('bookings__start'), size=Count('participations', distinct=True),
            open=Count('participations', filter=Q(participations__passed=None) & ~Q(participations__remark='rt'), distinct=True),
        ).filter(size__gt=0)
        if 'decide_results' in person.roles:
            decided = person.within(exams, 'decide_results')
            for exam in decided.filter(open=0):
                found.append(task(f'Ergebnisse freigeben: {exam.title}', exam.get_absolute_url()))
            for exam in decided.filter(open__gt=0, held__lte=now - timedelta(days=7)):  # a week after the exam, results are overdue
                found.append(task(f'Ergebnisse überfällig: {exam.title}, {plural(exam.open, "Teilnahme", "Teilnahmen")} offen', exam.get_absolute_url(), timezone.localdate(exam.held)))
        if 'manage_exams' in person.roles:
            from students.models import Document

            handed = Document.objects.filter(kind='abschlussarbeit', study__participations__course=OuterRef('pk'), created__gte=F('study__participations__created'))
            late = Course.objects.filter(kind=Course.Kind.THESIS, released=None, deadline__lt=timezone.localdate()).exclude(Exists(handed))
            for thesis in person.within(late, 'manage_exams').prefetch_related('participations__study__person'):
                who = ', '.join(p.study.person.last_name for p in thesis.participations.all())
                found.append(task(f'Abschlussarbeit nicht abgegeben: {who}, {thesis.title}', thesis.get_absolute_url(), thesis.deadline))
        if 'teacher' in person.roles:
            for exam in exams.filter(staff=person, held__lte=now):
                missing = exam.participations.exclude(scores__examiner=person).count()
                if missing:
                    found.append(task(f'{exam.title}: Ihre Wertung fehlt für {plural(missing, "Teilnahme", "Teilnahmen")}', exam.get_absolute_url()))
            handed = Q(participations__study__documents__kind='abschlussarbeit', participations__study__documents__created__gte=F('participations__created'))
            theses = Course.objects.filter(handed, kind=Course.Kind.THESIS, staff=person, released=None).exclude(participations__scores__examiner=person)
            for thesis in theses.distinct():
                found.append(task(f'Gutachten: {thesis.title}', thesis.get_absolute_url()))
        if 'student' in person.roles:
            from courses.models import Participation
            from .views import handed_in, offered

            if exams := list(offered(person)):
                due = min(e.deadline for e in exams if e.deadline)
                found.append(task(f'Prüfungsanmeldung: {plural(len(exams), "Prüfung", "Prüfungen")} möglich', reverse('exams:index'), due))
            for p in Participation.objects.filter(study__person=person, course__kind=Course.Kind.THESIS, course__released=None).select_related('course', 'study'):
                if not handed_in(p):
                    found.append(task(f'Abschlussarbeit abgeben: {p.course.title}', reverse('exams:index'), p.course.deadline))
        return found
