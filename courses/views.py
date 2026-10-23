import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import history, texts
from core.access import require
from core.dates import day as understood
from core.models import Institute, Term
from core.text import fold, matching
from core.views import without_term
from curriculum.models import Program
from rooms.models import Booking, BookingCategory
from rooms.views import agenda, week
from students.models import Study
from . import panel
from .forms import AddForm, CourseForm, PlanForm
from .models import Course, Participation

TEACHING = [Course.Kind.COURSE, Course.Kind.LESSON]
MANAGERS = {Course.Kind.EXAM: 'manage_exams', Course.Kind.RECOGNITION: 'recognise', Course.Kind.AUDITION: 'run_auditions'}


def deputat(courses):
    """Teaching load in SWS: each course's hours, per participant for one-to-one lessons."""
    courses = courses.annotate(load=Count('participations'))
    return sum((c.hours or 0) * (c.load if c.kind == Course.Kind.LESSON else 1) for c in courses)


def enrolled():
    """Everyone who can be entered into a course, for the name suggestions."""
    return Study.objects.filter(status=Study.Status.ENROLLED).select_related('person', 'program', 'subject').order_by('person__last_name')


def teaches(person, course):
    """Who runs a course: its staff, and whoever manages courses of its kind."""
    return MANAGERS.get(course.kind, 'manage_courses') in person.roles or course.staff.filter(pk=person.pk).exists()


def ahead():
    """Subquery: participations of the same course registered earlier (waitlist position)."""
    earlier = Participation.objects.filter(course=OuterRef('course'), created__lt=OuterRef('created'))
    return Subquery(earlier.values('course').annotate(n=Count('pk')).values('n')[:1])


def index(request):
    return redirect('courses:catalogue' if 'manage_courses' in request.user.roles else 'courses:mine')


def catalogue(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term is None:
        return without_term(request, 'ein Semester mit Veranstaltungen')
    studies = person.studies.filter(status=Study.Status.ENROLLED)
    program = request.GET.get('studiengang', str(studies[0].program_id) if studies else '')
    upcoming = Booking.objects.filter(course=OuterRef('pk'), start__gte=timezone.now()).order_by('start')
    courses = (
        term.courses.filter(kind__in=TEACHING).select_related('subject').prefetch_related('staff')
        .annotate(
            size=Count('participations'),
            next_start=Subquery(upcoming.values('start')[:1]),
            next_room=Subquery(upcoming.values('room__name')[:1]),
        )
    )
    if program.isdigit():
        courses = courses.filter(pk__in=Course.objects.filter(modules__program=program).values('pk'))
    if q := request.GET.get('q', '').strip():
        courses = matching(courses, q, 'title', 'staff__last_name', 'subject__name', 'description')
    return render(request, 'courses/catalogue.html', {
        'term': term,
        'terms': Term.objects.filter(courses__kind__in=TEACHING).distinct(),
        'courses': courses,
        'programs': Program.objects.all(),
        'program': program,
        'q': q,
        'office': 'manage_courses' in person.roles,
    })


def course(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind__in=TEACHING)
    staffed = teaches(person, course)
    staff, rows = panel.sheet(course)
    if request.method == 'POST':
        if not staffed or course.released:
            raise PermissionDenied
        try:
            panel.save(request.POST, rows, [], decide=True)
        except InvalidOperation:
            messages.error(request, 'Bitte nur Zahlen zwischen 0 und 99 eintragen.')
        else:
            messages.success(request, 'Ergebnisse gespeichert.')
        return redirect(course)
    rows.sort(key=lambda p: p.created)
    for n, p in enumerate(rows):
        p.waiting = course.capacity is not None and n >= course.capacity
    own = [p for p in rows if p.study.person_id == person.pk]
    return render(request, 'courses/course.html', {
        'course': course,
        'staff': staff,
        'rows': rows if staffed else [],
        'own': own,
        'count': len(rows),
        'staffed': staffed,
        'eligible': person.studies.filter(status=Study.Status.ENROLLED, program__modules__courses=course).distinct(),
        'modules': course.modules.select_related('program').order_by('code', 'program__degree', 'program__name'),
        'bookings': course.bookings.filter(start__gte=timezone.now()).select_related('room')[:6],
        'plan': PlanForm(person=person) if staffed else None,
        'adding': AddForm() if staffed else None,
        'enrolled': enrolled() if staffed else [],
        'panel': False,
    })


def edit(request, pk=None):
    """Create or change a course; the office may do either, staff may change their own."""
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING) if pk else Course(term=Term.pick(request.GET.get('semester')))
    if not ('manage_courses' in request.user.roles or (pk and teaches(request.user, course))):
        raise PermissionDenied
    form = CourseForm(request.POST or None, instance=course)
    if form.is_valid():
        course = form.save()
        messages.success(request, 'Gespeichert. Jetzt Termine planen und Teilnehmende eintragen.' if not pk else 'Gespeichert.')
        return redirect(course)
    back = course.get_absolute_url() if pk else reverse('courses:catalogue')
    return render(request, 'courses/form.html', {'form': form, 'course': course, 'menu': 'courses/_nav.html', 'heading': 'Neue Veranstaltung', 'back': back})


@require_POST
def add(request, pk):
    """Enter an enrolled student into any course, exam included, by name or Matrikelnummer."""
    course = get_object_or_404(Course, pk=pk)
    if not teaches(request.user, course):
        raise PermissionDenied
    form = AddForm(request.POST)
    found = Study.find(form.cleaned_data['who']) if form.is_valid() else []
    if len(found) != 1:
        names = ', '.join(str(s.person) for s in found)
        messages.error(request, f'Mehrere passen: {names}. Bitte genauer.' if found else 'Niemand Immatrikuliertes passt dazu.')
        return redirect(course)
    study = found[0]
    module = course.modules.filter(program=study.program).order_by('semester').first()
    _, created = Participation.objects.get_or_create(course=course, study=study, module=module)
    if created:
        texts.send('eingetragen', study.person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    messages.success(request, f'{study.person} eingetragen.' if created else f'{study.person} ist bereits eingetragen.')
    return redirect(course)


@require_POST
def register(request, pk):
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING)
    study = get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED)
    module = course.modules.filter(program=study.program).order_by('semester').first()
    if not course.is_open:
        messages.error(request, 'Der Anmeldeschluss ist vorbei.')
    elif module is None:
        messages.error(request, 'Diese Veranstaltung ist in Ihrem Studiengang nicht anrechenbar.')
    else:
        participation, _ = Participation.objects.get_or_create(course=course, study=study, module=module)
        place = course.participations.filter(created__lt=participation.created).count() + 1
        if course.capacity and place > course.capacity:
            messages.success(request, f'Sie stehen auf Platz {place - course.capacity} der Warteliste.')
        else:
            messages.success(request, 'Sie sind angemeldet.')
    return redirect(course)


@require_POST
def withdraw(request, pk):
    participation = get_object_or_404(Participation, pk=pk, study__person=request.user, course__kind__in=TEACHING)
    if participation.course.is_open:
        participation.delete()
        messages.success(request, 'Sie sind abgemeldet.')
    else:
        messages.error(request, 'Der Abmeldeschluss ist vorbei.')
    return redirect(participation.course)


@require_POST
def plan(request, pk):
    """Book the course's room weekly across the lecture period; all dates or none."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    form = PlanForm(request.POST, person=request.user)
    if not form.is_valid():
        messages.error(request, 'Bitte Wochentag, Beginn, Dauer und Raum angeben.')
        return redirect(course)
    term, data, series = course.term, form.cleaned_data, uuid.uuid4()
    first = term.lectures_start + timedelta(days=(data['weekday'] - term.lectures_start.weekday()) % 7)
    bookings, lesson = [], BookingCategory.system('unterricht')
    for n in range((term.lectures_end - first).days // 7 + 1):
        start = timezone.make_aware(datetime.combine(first + timedelta(weeks=n), data['time']))
        bookings.append(Booking(
            room=data['room'], start=start, end=start + timedelta(minutes=data['minutes']),
            person=request.user, course=course, series=series, category=lesson,
        ))
    if taken := [b for b in bookings if b.clashes().exists()]:
        messages.error(request, 'Nicht gebucht, belegt am ' + ', '.join(f'{b.start:%d.%m.}' for b in taken) + '.')
    else:
        history.saved(Booking.objects.bulk_create(bookings))
        messages.success(request, f'{len(bookings)} Termine in {data["room"]} gebucht.')
    return redirect(course)


def mine(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.objects.filter(courses__isnull=False).distinct()}
    if 'teacher' in person.roles:
        teaching = person.teaching.filter(term=term, kind__in=TEACHING)
        context.update(
            teaching=teaching.annotate(size=Count('participations')).select_related('subject'),
            load=deputat(teaching),
            students=Study.objects.filter(teacher=person, status=Study.Status.ENROLLED).select_related('person', 'program', 'subject'),
        )
    if 'student' in person.roles:
        context['registrations'] = (
            Participation.objects.filter(study__person=person, course__term=term, course__kind__in=TEACHING)
            .select_related('course', 'module').annotate(ahead=ahead())
        )
    return render(request, 'courses/mine.html', context)


def timetable(request):
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    return render(request, 'courses/timetable.html', {'day': day, 'clashes': True, **week(agenda(request.user), day)})


@require('view_teaching_load', 'teacher')
def load(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term is None:
        return without_term(request, 'ein Semester mit Veranstaltungen')
    courses = term.courses.filter(kind__in=TEACHING).prefetch_related('staff').annotate(size=Count('participations'))
    everyone = 'view_teaching_load' in person.roles
    if not everyone:
        courses = courses.filter(staff=person)
    institute, q = request.GET.get('institut', ''), request.GET.get('q', '').strip()
    words = fold(q).split()
    teachers = defaultdict(list)
    for c in courses:
        c.load = (c.hours or 0) * (c.size if c.kind == Course.Kind.LESSON else 1)
        for s in c.staff.all():
            if (everyone or s == person) and institute in ('', str(s.institute_id)) and all(w in fold(s.name) for w in words):
                teachers[s].append(c)
    rows = sorted(((t, cs, sum(c.load for c in cs)) for t, cs in teachers.items()), key=lambda row: row[0].last_name)
    return render(request, 'courses/load.html', {
        'term': term, 'terms': Term.objects.all(), 'rows': rows, 'total': sum(row[2] for row in rows), 'everyone': everyone,
        'institutes': Institute.objects.all(), 'institute': institute, 'q': q,
    })


def missing(target):
    """Courses `target` still lacks: lesson classes as this term has them, courses as the term a year before
    offered them, since most alternate between winter and summer."""
    before = Term.objects.filter(start__lte=target.start - timedelta(days=365)).last()
    return Course.objects.filter(
        Q(term=Term.current(), kind=Course.Kind.LESSON) | Q(term=before, kind=Course.Kind.COURSE)
    ).exclude(title__in=target.courses.values('title'))


@require('roll_over')
def rollover(request):
    """Copy the missing courses to the next term, with staff and modules; lesson classes keep their students."""
    target = Term.upcoming()
    if target is None:
        return without_term(request, 'das nächste Semester, in das die Veranstaltungen übernommen werden')
    pending = missing(target)
    if request.method == 'POST':
        with transaction.atomic():
            for c in pending.prefetch_related('modules', 'staff'):
                copy = Course.objects.create(
                    term=target, kind=c.kind, title=c.title, subject=c.subject, hours=c.hours,
                    capacity=c.capacity, description=c.description,
                )
                copy.modules.set(c.modules.all())
                copy.staff.set(c.staff.all())
                if c.kind == Course.Kind.LESSON:
                    history.saved(Participation.objects.bulk_create(
                        Participation(course=copy, study=p.study, module=p.module)
                        for p in c.participations.filter(study__status=Study.Status.ENROLLED).select_related('study')
                    ))
        messages.success(request, f'Veranstaltungen nach {target} übernommen.')
        return redirect(f"{reverse('courses:catalogue')}?semester={target.pk}")
    return render(request, 'courses/rollover.html', {'target': target, 'pending': pending.select_related('term').order_by('title')})
