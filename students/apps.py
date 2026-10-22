from django.apps import AppConfig

from django.urls import reverse
from django.utils.formats import date_format, number_format

from core.apps import Tile, feature, plural, task


class StudentsConfig(Tile, AppConfig):
    name = 'students'
    verbose_name = 'Studierende'
    order = 1
    icon = 'M22 10 12 5 2 10l10 5 10-5zM6 12v5c3 3 9 3 12 0v-5'
    url = 'students:index'

    def title(self, roles):
        if 'view_students' in roles:
            return 'Studierende'
        if 'student' in roles:
            return 'Mein Studium'

    def figure(self, person):
        from core.models import Term
        from .models import Study, StudyTerm

        term = Term.upcoming()
        paid = StudyTerm.objects.filter(term=term, paid__isnull=False).values('study__person')
        if 'view_students' in person.roles:
            enrolled = Study.objects.filter(status=Study.Status.ENROLLED)
            if not (term and term.reregistration_open):
                return f'{enrolled.count()} immatrikuliert'
            unpaid = enrolled.exclude(person__in=paid).values('person').distinct().count()
            return f'{enrolled.count()} immatrikuliert · {unpaid} ohne Rückmeldung'
        if not term:
            return ''
        if not term.reregistration_open:
            return f'Rückmeldung {term} ab {date_format(term.reregistration_start, "j.n.Y")}'
        return f'Rückmeldung {term}: {"erledigt" if paid.filter(study__person=person).exists() else "offen"}'

    def features(self, person):
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
        for study in person.studies.filter(status='enrolled'):
            found += [
                feature(f'Mein Studium {study.subject}', study.get_absolute_url(), 'akte status ects fachsemester rückmeldung urlaubssemester beurlaubung'),
                feature('Immatrikulationsbescheinigung', reverse('students:certificate', args=[study.pk]), 'bescheinigung studienbescheinigung nachweis drucken pdf'),
            ]
        return found

    def tasks(self, person):
        from core.models import Term
        from .models import Study, StudyTerm

        term = Term.upcoming()
        paid = StudyTerm.objects.filter(term=term, paid__isnull=False).values('study__person')
        enrolled = Study.objects.filter(status=Study.Status.ENROLLED)
        found = []
        if 'manage_students' in person.roles:
            accepted = Study.objects.filter(status=Study.Status.ADMITTED, terms__isnull=False).distinct().count()
            if accepted:
                found.append(task(f'{plural(accepted, "angenommenen Studienplatz", "angenommene Studienplätze")} immatrikulieren', reverse('admissions:board')))
        if 'manage_reregistration' in person.roles:
            unpaid = enrolled.exclude(person__in=paid).values('person').distinct().count()
            if unpaid and term and term.reregistration_open:
                found.append(task(f'Rückmeldung {term}: {plural(unpaid, "Zahlung", "Zahlungen")} ausstehend', reverse('students:reregistration'), term.deadline))
        study = enrolled.filter(person=person).first()
        if study and term and term.reregistration_open and not paid.filter(study__person=person).exists():
            found.append(task(f'Rückmeldung {term}: Semesterbeitrag {number_format(term.fee, 2)} € überweisen', study.get_absolute_url(), term.deadline))
        return found
