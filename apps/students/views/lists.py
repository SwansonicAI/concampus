"""The list of students: filters, cases needing attention, and writing to many at once."""
import csv
from collections import defaultdict

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core.access import CODENAMES, require
from core.forms import ComposeForm
from core.apps import plural
from core.models import Export, Office, Person, Term
from core.shortcuts import SHOWN
from core.text import matching
from courses.lessons import untimed
from courses.models import Participation
from curriculum.models import Module, Program, Subject, preload
from ..models import Document, Study


def own(person):
    """The person's own study to open: the one under way, else the latest they finished or left."""
    return (
        person.studies.filter(status=Study.Status.ENROLLED).first()
        or person.studies.filter(status__in=Study.FORMER, terms__isnull=False).order_by('-since').first()
    )


@require('teacher', 'student', *CODENAMES)
def suggest(request):
    """Enrolled students matching a name as it is typed, for adding someone to a course or a booking: from three letters,
    a few at a time, one's own class, courses and ensembles first. Never the whole list (docs/privacy.md)."""
    person, q = request.user, request.GET.get('q', '').strip()
    if len(q) < 3:
        return JsonResponse({'found': []})
    studies = Study.objects.filter(status=Study.Status.ENROLLED).exclude(person=person).select_related('person', 'subject', 'program')
    near = Q(teacher=person) | Q(participations__course__staff=person, participations__course__term=Term.current()) | Q(ensembles__members__person=person)
    found = matching(studies, q, 'person__first_name', 'person__last_name', 'person__birth_name', starts=True).annotate(near=Count('pk', filter=near))
    shown = {}
    for s in found.order_by('-near', 'person__last_name', 'person__first_name')[:24]:
        shown.setdefault(s.person_id, s)
    return JsonResponse({'found': [
        {'id': s.person_id, 'text': f'{s.person.name} · {s.subject} {s.program.degree}', 'value': f'{s.person.first_name} {s.person.last_name}, {s.subject} {s.program.degree}'}
        for s in list(shown.values())[:8]
    ]})


def index(request):
    roles = request.user.roles
    if 'view_students' in roles:
        return redirect('students:list')
    study = own(request.user)
    if study is None:
        raise PermissionDenied
    return redirect(study.get_absolute_url())


COLUMNS = [  # heading, sort key, field; the server sorts, since the list may be longer than shown
    ('Matrikelnr.', 'matrikel', 'person__matriculation'), ('Name', 'name', 'person__last_name'), ('Studiengang', 'studiengang', 'program__name'),
    ('Hauptfach', 'hauptfach', 'subject__name'), ('Klasse', 'klasse', 'teacher__last_name'), ('FS', 'fs', 'fs'), ('Status', 'status', 'status'),
]


CASES = [
    ('unterricht', 'Ohne Unterrichtszeit'), ('pruefung', 'Prüfung laut Plan überfällig'), ('regelstudienzeit', 'Über der Regelstudienzeit'),
    ('schwerpunkt', 'Schwerpunkt nicht gewählt'), ('beurlaubt', 'Beurlaubt in diesem Semester'),
]
STATES = [('', 'Alle'), *((s.value, s.label) for s in [*Study.ATTENDING, Study.Status.GRADUATED, Study.Status.LEFT])]  # who studies or studied here


def semesters(studies):
    """The studies with their Fachsemester as `fs`, counted in the database."""
    return studies.annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__term__start__lte=timezone.localdate())))


def cases(studies, case):
    """The studies in one of the situations an office keeps an eye on; `studies` carries its Fachsemester as `fs`."""
    if case == 'unterricht':
        return studies.filter(pk__in=untimed(Participation.objects.all()).values('study'))
    if case == 'beurlaubt':
        return studies.filter(terms__term=Term.current(), terms__leave=True)
    if case == 'regelstudienzeit':
        return studies.filter(fs__gt=F('program__semesters'))
    if case == 'schwerpunkt':
        due = Q(pk__in=[])
        preload(programs := list(Program.objects.filter(pk__in=studies.values('program'))))
        for program in programs:
            if choices := program.choices():
                due |= Q(program=program, fs__gte=min(c.first or 99 for c in choices))
        return studies.filter(due, focus=None)
    if case == 'pruefung':  # an exam of a module under way that the plan recommends for an earlier semester
        rows = list(studies.values_list('pk', 'program', 'subject', 'fs'))
        exams = defaultdict(list)
        for m in Module.objects.filter(exam=True, semester__isnull=False, program__in={r[1] for r in rows}).prefetch_related('waived'):
            exams[m.program_id].append(m)
        seen = defaultdict(set)  # each study's modules passed or taken now, and ('under', parent) for the modules under way
        for study, module, parent, passed in Participation.objects.filter(study__in=[r[0] for r in rows]).exclude(passed=False).values_list('study', 'module', 'module__parent', 'passed'):
            seen[study] |= {module, ('under', parent)} if passed else {module}
        late = [
            pk for pk, program, subject, fs in rows for m in exams[program]
            if m.semester < fs and m.pk not in seen[pk] and ('under', m.parent_id) in seen[pk] and subject not in {s.pk for s in m.waived.all()}
        ]
        return studies.filter(pk__in=late)
    return studies


def narrowed(choices, request, program, subject=None):
    """A filter's choices as far as the Studiengang and Hauptfach already chosen allow: Hauptfächer of the Studiengang,
    teachers of its students in the Hauptfach."""
    if (chosen := request.GET.get('studiengang', '')).isdigit():
        choices = choices.filter(**{program: chosen})
    if subject and (chosen := request.GET.get('fach', '')).isdigit():
        choices = choices.filter(**{subject: chosen})
    return choices.distinct()


def listed(request):
    """(the studies the list shows as filtered, all of them and not only the rows on the page; its status; the studies
    the person may list at that status)."""
    status = request.GET.get('status', Study.Status.ENROLLED)
    status = status if status in dict(STATES) else Study.Status.ENROLLED
    shown = Study.objects.filter(status=status) if status else Study.objects.exclude(status__in=Study.APPLICATION)
    scoped = request.user.within(shown, 'view_students')
    studies = semesters(scoped.select_related('person', 'program', 'subject', 'start_term', 'teacher')).order_by('person__last_name', 'person__first_name')
    for key, field in [('studiengang', 'program'), ('fach', 'subject'), ('klasse', 'teacher'), ('fs', 'fs'), ('beginn', 'start_term')]:
        if (value := request.GET.get(key, '')).isdigit():
            studies = studies.filter(**{field: value})
    studies = cases(studies, request.GET.get('stand', ''))
    studies = matching(studies, request.GET.get('q', '').strip(), 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email', 'person__matriculation', 'teacher__last_name')
    return studies, status, scoped


@require('view_students')
def roster(request):
    studies, status, scoped = listed(request)
    q, sort = request.GET.get('q', '').strip(), request.GET.get('sortierung', '')
    if field := next((f for _, key, f in COLUMNS if key == sort.removeprefix('-')), None):
        studies = studies.order_by(F(field).desc(nulls_last=True) if sort.startswith('-') else F(field).asc(nulls_last=True), 'person__last_name', 'person__first_name')
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="studierende.csv"'
        response.write('﻿')
        writer = csv.writer(response, delimiter=';')
        personal = 'manage_students' in request.user.roles  # gender and nationality for the student office only (docs/privacy.md)
        writer.writerow(['Matrikelnummer', 'Nachname', 'Vorname', *(['Geschlecht', 'Staatsangehörigkeit'] if personal else []), 'E-Mail', 'Studiengang', 'Hauptfach', 'Fachsemester', 'Status'])
        Export.log(request.user, f'Studierende ({request.GET.urlencode()})', studies.count())
        for s in studies:
            writer.writerow([
                s.person.matriculation, s.person.last_name, s.person.first_name, *([s.person.get_gender_display(), s.person.get_nationality_display()] if personal else []),
                s.person.email, s.program, s.subject, s.fs, s.state,
            ])
        return response
    shown = list(studies[:SHOWN])
    return render(request, 'students/roster.html', {
        'studies': shown, 'count': len(shown) if len(shown) < SHOWN else studies.count(), 'q': q, 'status': status, 'statuses': STATES,
        'sort': sort, 'columns': [(title, key, 'ascending' if sort == key else 'descending' if sort == f'-{key}' else '') for title, key, _ in COLUMNS],
        'programs': request.user.within(Program.objects.ordered(), 'view_students'), 'subjects': narrowed(request.user.within(Subject.objects.all(), 'view_students'), request, 'programs'),
        'terms': Term.objects.filter(intake__isnull=False).distinct().order_by('-start'), 'semesters': range(1, 13),
        'classes': Person.objects.filter(class_studies__in=narrowed(scoped, request, 'program', 'subject')).distinct().order_by('last_name', 'first_name'),
        'cases': CASES,
        **{key: request.GET.get(key, '') for key in ['studiengang', 'fach', 'klasse', 'fs', 'beginn', 'stand']},
    })


@require('manage_students')
def letters(request):
    """The latest Bescheid of each chosen study, ready to print."""
    studies = request.user.within(Study.objects.filter(pk__in=request.GET.getlist('study')), 'manage_students').select_related('person')
    issued = Document.objects.filter(study__in=studies, message__isnull=False).select_related('message__person').order_by('created')
    latest = {d.study_id: d.message for d in issued}
    return render(request, 'core/letter.html', {'letters': latest.values(), 'missing': [s.person.name for s in studies if s.pk not in latest]})


@require('message_students')
def compose(request):
    """One message to the chosen people, e.g. everyone in their first semester: the ticked, or with ?alle every study the
    list's filter finds, beyond the rows it shows."""
    chosen = listed(request)[0].values('pk') if request.GET.get('alle') else request.POST.getlist('study') or request.GET.getlist('study')
    studies = request.user.within(Study.objects.filter(pk__in=chosen), 'message_students').select_related('person')
    people = sorted({s.person for s in studies}, key=lambda p: p.name)
    back = request.POST.get('next') or request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}):
        back = ''
    form = ComposeForm(request.POST or None)
    sender = ', '.join(Office.objects.filter(user=request.user, permissions__codename='message_students').values_list('name', flat=True)) or str(request.user)
    if people and form.is_valid():
        form.send(people, sender)
        messages.success(request, f'Nachricht an {plural(len(people), "Person", "Personen")} gesendet.')
        return redirect(back or 'students:list')
    return render(request, 'students/compose.html', {'form': form, 'people': people, 'studies': studies, 'back': back, 'sender': sender})
