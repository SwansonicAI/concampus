from collections import Counter, defaultdict
from datetime import timedelta

from django.apps import AppConfig
from django.db.models import Count, F, Q

from django.urls import reverse
from django.utils.formats import date_format, number_format

from core.apps import Tile, feature, plural, task

WAITS = {  # the first word of what an accepted place still lacks: what it waits for, as the office's to-do says it
    'Versicherungsmeldung': 'die Krankenkasse', 'Semesterbeitrag': 'den Semesterbeitrag', 'Hochschulzugangsberechtigung': 'den Hochschulzugang',
    'Sperre:': 'die Aufhebung einer Sperre', 'Leihinstrument': 'ein Leihinstrument',
}


class StudentsConfig(Tile, AppConfig):
    name = 'students'
    verbose_name = 'Studierende'
    order = 1
    icon = 'M22 10 12 5 2 10l10 5 10-5zM6 12v5c3 3 9 3 12 0v-5'
    url = 'students:index'

    def title(self, roles):
        if 'view_students' in roles:
            return 'Studierende'
        if roles & {'student', 'alumni'}:
            return 'Mein Studium'

    def figure(self, person):
        from core.models import Term
        from .models import Study, StudyTerm

        term = Term.upcoming()
        paid = StudyTerm.objects.filter(term=term, paid__isnull=False).values('study__person')
        if 'view_students' in person.roles:
            enrolled = person.within(Study.objects.filter(status=Study.Status.ENROLLED), 'view_students')
            people = enrolled.values('person').distinct().count()  # people, as Auswertungen counts them, not their studies
            if not (term and term.reregistration_open):
                return f'{people} immatrikuliert'
            unpaid = enrolled.exclude(person__in=paid).values('person').distinct().count()
            return f'{people} immatrikuliert · {unpaid} ohne Rückmeldung'
        if 'student' not in person.roles:
            from .views import own

            study = own(person)
            return f'{study.state} am {date_format(study.since, "j.n.Y")}'
        if not term:
            return ''
        if not term.reregistration_open:
            return f'Rückmeldung {term} ab {date_format(term.reregistration_start, "j.n.Y")}'
        return f'Rückmeldung {term}: {"erledigt" if paid.filter(study__person=person).exists() else "offen"}'

    def features(self, person):
        from .models import Study

        found = []
        if 'view_students' in person.roles:
            found += [
                feature('Studierende', reverse('students:list'), 'liste suchen filtern matrikel akte'),
                feature('Studierende als CSV exportieren', f"{reverse('students:list')}?format=csv", 'export excel tabelle download'),
            ]
        if 'manage_reregistration' in person.roles:
            found += [
                feature('Rückmeldung', reverse('students:reregistration'), 'semesterbeitrag zahlung bezahlt überweisung exmatrikulieren'),
            ]
        for study in person.studies.filter(Q(status='enrolled') | Q(status__in=Study.FORMER, terms__isnull=False)).distinct().select_related('subject'):
            found.append(feature(f'Mein Studium {study.subject}', study.get_absolute_url(), 'akte status ects fachsemester rückmeldung urlaubssemester beurlaubung abschluss'))
            if study.status == 'enrolled':
                found.append(feature('Immatrikulationsbescheinigung', reverse('students:certificate', args=[study.pk]), 'bescheinigung studienbescheinigung nachweis drucken pdf'))
            found.append(feature(f'Studienzeitbescheinigung {study.subject}', reverse('students:periods', args=[study.pk]), 'studienzeiten semester rentenversicherung nachweis drucken pdf'))
        return found

    def tasks(self, person):
        from django.utils import timezone

        from core.models import Institution, Term
        from .models import Study, StudyTerm

        term, today = Term.upcoming(), timezone.localdate()
        due = term and (term.deadline if today <= term.deadline else term.closes)  # in the Nachfrist, its end
        paid = StudyTerm.objects.filter(term=term, paid__isnull=False).values('study__person')
        enrolled = Study.objects.filter(status=Study.Status.ENROLLED)
        found = []
        if 'manage_students' in person.roles:
            accepted = person.within(Study.objects.filter(status=Study.Status.ADMITTED, terms__term=F('start_term')), 'manage_students').select_related('start_term', 'person', 'program')
            terms = defaultdict(lambda: ([], Counter()))  # ready to enrol; what the others wait for
            for s in accepted.order_by('start_term__start'):
                ready, waiting = terms[s.start_term]
                if missing := s.missing():
                    waiting.update({WAITS.get(reason.split()[0], 'Unterlagen') for reason in missing})
                else:
                    ready.append(s)
            for term, (ready, waiting) in terms.items():
                url = f"{reverse('admissions:board')}?semester={term.pk}#immatrikulieren"
                if ready:
                    found.append(task(f'{plural(len(ready), "angenommenen Studienplatz", "angenommene Studienplätze")} zum {term} immatrikulieren', url, term.start))
                if waiting:  # not overdue: it is up to others
                    found.append(task(f'Immatrikulation zum {term}: ' + ', '.join(f'{n} {"wartet" if n == 1 else "warten"} auf {what}' for what, n in waiting.items()), url))
            from courses.models import Participation

            fails = (  # a module failed as often as the Prüfungsordnung allows
                Participation.objects.filter(passed=False, course__released__isnull=False, study__in=person.within(enrolled, 'manage_students'))
                .values('study', 'module', 'study__program__attempts').annotate(n=Count('pk')).filter(n__gte=F('study__program__attempts'))
            )
            passed = set(Participation.objects.filter(passed=True, study__in={f['study'] for f in fails}).values_list('study', 'module'))
            from exams.views import exhausted

            for study in Study.objects.filter(pk__in={f['study'] for f in fails if (f['study'], f['module']) not in passed}).select_related('person', 'program'):
                final = max(m.appeal for m in exhausted(study)) + timedelta(days=1)  # once the Widerspruchsfrist is over
                found.append(task(f'Exmatrikulieren{"" if final <= today else " ab " + date_format(final, "j.n.")}: {study.person}, Prüfung endgültig nicht bestanden', study.get_absolute_url(), final))
        if 'view_students' in person.roles and (not person.everywhere('view_students') or 'decide_results' in person.roles):  # an Institut's office, and the office deciding results: who should have chosen
            from .views import cases, semesters

            if n := cases(semesters(person.within(enrolled, 'view_students')), 'schwerpunkt').count():
                found.append(task(f'Schwerpunkt nicht gewählt: {plural(n, "Person", "Personen")}', f"{reverse('students:list')}?stand=schwerpunkt"))
        if 'manage_students' in person.roles:  # the Zeugnis issued: the study ends
            for study in person.within(enrolled.filter(issued__isnull=False), 'manage_students').select_related('person', 'program'):
                found.append(task(f'Exmatrikulieren nach Abschluss: {study.person}, {study.program.title}', study.get_absolute_url(), study.issued))
        if 'manage_reregistration' in person.roles:
            unpaid = enrolled.exclude(person__in=paid).values('person').distinct().count()
            if unpaid and term and term.reregistration_open:
                found.append(task(f'Rückmeldung {term}: {plural(unpaid, "Zahlung", "Zahlungen")} ausstehend', reverse('students:reregistration'), due))
        for own in enrolled.filter(person=person, focus=None).select_related('program'):  # in the semester before it begins
            if (options := own.options()) and own.semester >= (first := min(o.first or 99 for o in options)) - 1:
                found.append(task(f'{options[0].choice.title} wählen', f'{own.get_absolute_url()}#studienverlauf', Study.choose_by(own.semester, first)))
        study = enrolled.filter(person=person).first()
        from exams.views import exhausted

        if study and term and term.reregistration_open and not paid.filter(study__person=person).exists() and not exhausted(study):  # no Rückmeldung once the claim is lost
            fee = term.due(today, Institution.get().late_fee)
            found.append(task(f'Rückmeldung {term}: {number_format(fee, 2)} € überweisen' + (' (Nachfrist, mit Säumnisgebühr)' if fee > term.fee else ''), study.get_absolute_url(), due))
        return found
