from datetime import timedelta

from django.apps import AppConfig
from django.db.models import Count, Exists, F, Min, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

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
        from .views import EXAMS

        term = Term.current()
        if not term:
            return ''
        if person.roles & {'manage_exams', 'decide_results'}:  # counted as the list of Prüfungen counts them, theses included
            exams = person.within(term.courses.filter(kind__in=EXAMS), 'manage_exams', 'decide_results')
            return f'{plural(exams.count(), "Prüfung", "Prüfungen")} im {term}'
        if 'teacher' in person.roles:
            return f'{plural(person.teaching.filter(term=term, kind__in=EXAMS).count(), "Kommission", "Kommissionen")} im {term}'
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
        from courses import panel
        from courses.models import Course, Participation
        from curriculum.models import Module

        found, now = [], timezone.now()
        exams = Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], released=None).annotate(
            held=Min('bookings__start'), size=Count('participations', distinct=True),
            open=Count('participations', filter=Q(participations__passed=None) & ~Q(participations__remark='rt'), distinct=True),
        ).filter(size__gt=0)
        if 'decide_results' in person.roles:
            from students.models import Study
            from .views import achievements

            totals = {}  # each programme's credits; only who has passed as many is checked exactly
            for program, credits in Module.objects.filter(parent=None).values_list('program').annotate(n=Sum('credits')):
                totals[program] = credits
            credited = Participation.objects.filter(study=OuterRef('pk'), passed=True).exclude(course__kind__in=['exam', 'thesis'], course__released=None)
            near = person.within(Study.objects.filter(status=Study.Status.ENROLLED, issued=None), 'decide_results').annotate(
                credited=Subquery(credited.values('study').annotate(n=Sum('module__credits')).values('n')[:1]),
            ).select_related('person', 'program')
            for study in near:
                if study.credited and study.credited >= totals.get(study.program_id, 0) and achievements(study)['finished']:
                    found.append(task(f'Zeugnis ausstellen: {study.person}, {study.program.title}', reverse('exams:transcript', args=[study.pk])))
            decided = person.within(exams, 'decide_results')
            for exam in decided.filter(open=0):
                found.append(task(f'Ergebnisse freigeben: {exam.title}', exam.get_absolute_url()))
            for thesis in decided.filter(kind=Course.Kind.THESIS).annotate(marked=Count('participations__scores')).filter(marked__gte=2):
                if any(p.third for p in panel.sheet(thesis)[1]):  # Gutachten too far apart, or one failing it (RPSO § 15 Abs. 2)
                    who = ', '.join(p.study.person.last_name for p in thesis.participations.select_related('study__person'))
                    found.append(task(f'Drittes Gutachten bestellen: {who}, {thesis.title}', reverse('exams:edit', args=[thesis.pk])))
            for exam in decided.filter(open__gt=0, held__lte=now - timedelta(days=7)):  # a week after the exam, results are overdue
                late = ', '.join(e.last_name for e, _ in panel.waiting(panel.sheet(exam)[1]))
                found.append(task(
                    f'Ergebnisse überfällig: {exam.title}, ' + (f'Wertung fehlt: {late}' if late else f'{plural(exam.open, "Teilnahme", "Teilnahmen")} offen'),
                    exam.get_absolute_url(), timezone.localdate(exam.held),
                ))
        if 'manage_exams' in person.roles:
            from core.models import Term
            from students.models import Document

            if short := person.within(Course.short_panels(), 'manage_exams').filter(released=None).count():
                found.append(task(f'{plural(short, "Prüfung", "Prüfungen")}: Kommission zu klein', f"{reverse('exams:index')}?stand=kommission"))
            failed = Participation.objects.filter(study__person=OuterRef('study__person'), module=OuterRef('module'), passed=False, course__released__isnull=False)
            last = (
                person.within(Participation.objects.filter(course__kind=Course.Kind.EXAM, course__released=None), 'manage_exams')
                .annotate(failed=Coalesce(Subquery(failed.values('module').annotate(n=Count('pk')).values('n')[:1]), 0)).filter(failed__gte=F('study__program__attempts') - 1)
                .values('course', 'course__title').annotate(n=Count('pk'))
            )
            for row in last:
                found.append(task(f'{row["course__title"]}: {plural(row["n"], "Anmeldung", "Anmeldungen")} im letzten Versuch', reverse('exams:exam', args=[row['course']])))

            undated = person.within(Course.objects.filter(kind=Course.Kind.EXAM, term=Term.current(), bookings=None, released=None), 'manage_exams')
            if soon := list(undated.filter(Q(opens=None) | Q(opens__lte=timezone.localdate() + timedelta(days=14))).values_list('opens', flat=True)):  # registration open or near
                first = min(filter(None, soon), default=None)
                when = f' vor der Anmeldung ab {date_format(first, "j.n.")}' if first and first > timezone.localdate() else ''
                found.append(task(f'{plural(len(soon), "Prüfung", "Prüfungen")} ohne Termin{when}', reverse('exams:index'), first))

            handed = Document.objects.filter(kind='abschlussarbeit', study__participations__course=OuterRef('pk'), created__gte=F('study__participations__created'))
            late = Course.objects.filter(kind=Course.Kind.THESIS, released=None, deadline__lt=timezone.localdate()).exclude(Exists(handed))
            for thesis in person.within(late, 'manage_exams').prefetch_related('participations__study__person'):
                who = ', '.join(p.study.person.last_name for p in thesis.participations.all())
                found.append(task(f'Abschlussarbeit nicht abgegeben: {who}, {thesis.title}', thesis.get_absolute_url(), thesis.deadline))
        if 'manage_exams' in person.roles:
            vacant = person.within(Course.objects.filter(
                kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], released=None, participations__recusals__isnull=False, participations__recusals__substitute=None,
            ), 'manage_exams')
            for exam in vacant.distinct():
                found.append(task(f'{exam.title}: Ersatz für eine befangene Person bestellen', exam.get_absolute_url()))
        if 'teacher' in person.roles:
            standing_in = Participation.objects.filter(recusals__substitute=person, course__released=None).exclude(scores__examiner=person).exclude(course__kind=Course.Kind.AUDITION)
            for row in standing_in.values('course', 'course__title').annotate(n=Count('pk')):
                found.append(task(f'{row["course__title"]}: Ihre Wertung als Ersatz fehlt für {plural(row["n"], "Teilnahme", "Teilnahmen")}', reverse('exams:exam', args=[row['course']])))
            for exam in exams.filter(staff=person, held__lte=now):
                missing = exam.participations.exclude(scores__examiner=person).exclude(recusals__examiner=person).count()
                if missing:
                    found.append(task(f'{exam.title}: Ihre Wertung fehlt für {plural(missing, "Teilnahme", "Teilnahmen")}', exam.get_absolute_url()))
            handed = Q(participations__study__documents__kind='abschlussarbeit', participations__study__documents__created__gte=F('participations__created'))
            theses = (
                Course.objects.filter(handed, kind=Course.Kind.THESIS, staff=person, released=None)
                .exclude(participations__scores__examiner=person).exclude(participations__recusals__examiner=person)
            )
            for thesis in theses.distinct().prefetch_related('participations__study__person'):
                who = ', '.join(str(p.study.person) for p in thesis.participations.all())
                found.append(task(f'Gutachten: {who}, {thesis.title}', thesis.get_absolute_url()))
        if 'student' in person.roles:
            from courses.models import Participation
            from .views import handed_in, offered

            if exams := list(offered(person)):
                due = min(e.deadline for e in exams if e.deadline)
                opens = None if any(e.is_open for e in exams) else min(e.opens for e in exams)
                when = f' ab {date_format(opens, "j.n.")}' if opens else ''
                found.append(task(f'Prüfungsanmeldung{when}: {plural(len(exams), "Prüfung", "Prüfungen")}', reverse('exams:index'), due))
            for p in Participation.objects.filter(study__person=person, course__kind=Course.Kind.THESIS, course__released=None).select_related('course', 'study'):
                if not handed_in(p):
                    found.append(task(f'Abschlussarbeit abgeben: {p.course.title}', reverse('exams:index'), p.course.deadline))
        return found
