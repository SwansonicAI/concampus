import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import history, texts
from core.access import kept, require
from core.dates import day as understood
from core.models import Holiday, Institute, Person, Term
from core.text import fold, matching
from core.views import without_term
from curriculum.models import Program
from rooms.models import Booking, BookingCategory
from rooms.views import agenda, week
from students.models import Study
from . import panel
from .forms import AddForm, CourseForm, DateForm, EnsembleForm, PlanForm
from .models import Area, Course, Ensemble, Format, Participation, Signup

TEACHING = [Course.Kind.COURSE, Course.Kind.LESSON]
MANAGERS = {Course.Kind.EXAM: 'manage_exams', Course.Kind.RECOGNITION: 'recognise', Course.Kind.AUDITION: 'run_auditions'}


def deputat(courses, person):
    """A teacher's load in SWS: each course's hours; per participant in one-to-one lessons, per ensemble they coach in
    ensemble courses."""
    courses = courses.annotate(size=Count('participations', distinct=True), coached=Count('ensembles', filter=Q(ensembles__coach=person), distinct=True))
    return sum((c.hours or 0) * share(c, c.coached) for c in courses)


def share(course, coached):
    """How many times a course's hours count for one of its teachers."""
    if course.kind == Course.Kind.LESSON:
        return course.size
    return coached if course.allocation == Course.Allocation.ENSEMBLE else 1


def credited(course):
    """The modules a course counts for, by title: [(title, [(program, codes)])], so a course in many plans reads in a few lines."""
    found = defaultdict(lambda: defaultdict(list))
    for m in course.modules.select_related('program').order_by('title', 'program__degree', 'program__name', 'code'):
        found[m.title][m.program].append(m.code)
    return [(title, list(programs.items())) for title, programs in found.items()]


def enrolled():
    """Everyone who can be entered into a course, for the name suggestions."""
    return Study.objects.filter(status=Study.Status.ENROLLED).select_related('person', 'program', 'subject').order_by('person__last_name')


def teaches(person, course):
    """Who runs a course: its staff, and whoever manages courses of its kind where it belongs."""
    return course.staff.filter(pk=person.pk).exists() or person.may(MANAGERS.get(course.kind, 'manage_courses'), course)


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
        term.courses.filter(kind__in=TEACHING).select_related('subject', 'area', 'format').prefetch_related('staff')
        .annotate(
            size=Count('participations'),
            next_start=Subquery(upcoming.values('start')[:1]),
            next_room=Subquery(upcoming.values('room__name')[:1]),
        )
    )
    offered = courses
    if program.isdigit():
        courses = courses.filter(pk__in=Course.objects.filter(modules__program=program).values('pk'))
    area, form = request.GET.get('fachgebiet', ''), request.GET.get('lehrform', '')
    if area.isdigit():
        courses = courses.filter(area=area)
    if form.isdigit():
        courses = courses.filter(format=form)
    if q := request.GET.get('q', '').strip():
        courses = matching(courses, q, 'title', 'staff__last_name', 'subject__name', 'area__name', 'description')
    return render(request, 'courses/catalogue.html', {
        'term': term,
        'terms': Term.objects.filter(courses__kind__in=TEACHING).distinct(),
        'courses': courses.order_by(F('area__order').asc(nulls_last=True), 'area__name', 'title'),
        'programs': Program.objects.all(),
        'program': program,
        'areas': Area.objects.filter(courses__in=offered.values('pk')).distinct(),
        'area': area,
        'formats': Format.objects.filter(courses__in=offered.values('pk')).distinct(),
        'form': form,
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
        'wished': course.signups.filter(study__person=person).first(),
        'signups': course.signups.select_related('study__person', 'study__program', 'study__subject') if staffed else [],
        'count': len(rows),
        'staffed': staffed,
        'eligible': person.studies.filter(status=Study.Status.ENROLLED, program__modules__courses=course).distinct(),
        'modules': credited(course),
        'bookings': course.bookings.filter(start__gte=timezone.now()).select_related('room')[:6],
        'plan': PlanForm(person=person) if staffed else None,
        'date': DateForm(person=person) if staffed else None,
        'ensembles': (course.ensembles if staffed else course.ensembles.filter(members__person=person).distinct())
        .select_related('coach', 'wish').prefetch_related('members__person', 'members__subject'),
        'grouping': EnsembleForm(course=course) if course.allocation == Course.Allocation.ENSEMBLE else None,
        'adding': AddForm() if staffed else None,
        'enrolled': enrolled() if staffed else [],
        'panel': False,
    })


def edit(request, pk=None):
    """Create or change a course; the office may do either for its Institute and Studiengänge, staff may change their own."""
    person = request.user
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING) if pk else Course(term=Term.pick(request.GET.get('semester')))
    staffed = pk and course.staff.filter(pk=person.pk).exists()
    if not (staffed or 'manage_courses' in person.roles and (not pk or person.may('manage_courses', course))):
        raise PermissionDenied
    form = CourseForm(request.POST or None, instance=course)
    if form.is_valid() and (saved := form.save() if staffed else kept(form, person, 'manage_courses')):
        course = saved
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
    if course.allocation == Course.Allocation.STAFF:
        messages.error(request, 'Die Lehrenden teilen die Plätze ein.')
    elif course.allocation == Course.Allocation.ENSEMBLE:
        messages.error(request, 'Bitte als Ensemble anmelden.')
    elif course.upcoming:
        messages.error(request, f'Die Anmeldung beginnt am {course.opens:%d.%m.%Y}.')
    elif not course.is_open:
        messages.error(request, 'Der Anmeldeschluss ist vorbei.')
    elif module is None:
        messages.error(request, 'Diese Veranstaltung ist in Ihrem Studiengang nicht anrechenbar.')
    elif course.allocation == Course.Allocation.CHOICE:
        Signup.objects.get_or_create(course=course, study=study, defaults={'module': module, 'note': request.POST.get('note', '').strip()})
        messages.success(request, 'Angefragt. Die Lehrenden vergeben die Plätze; die Antwort kommt in Ihr Postfach.')
    else:
        participation, _ = Participation.objects.get_or_create(course=course, study=study, module=module)
        place = course.participations.filter(created__lt=participation.created).count() + 1
        if course.capacity and place > course.capacity:
            messages.success(request, f'Sie stehen auf Platz {place - course.capacity} der Warteliste.')
        else:
            messages.success(request, 'Sie sind angemeldet.')
    return redirect(course)


@require_POST
def ensemble(request, pk):
    """Register an ensemble: everyone in it takes part in the course, and the others hear who registered them."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING, allocation=Course.Allocation.ENSEMBLE)
    study = get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED)
    form = EnsembleForm(request.POST, course=course)
    if not course.is_open:
        messages.error(request, 'Der Anmeldeschluss ist vorbei.' if not course.upcoming else f'Die Anmeldung beginnt am {course.opens:%d.%m.%Y}.')
        return redirect(course)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect(course)
    members = list(dict.fromkeys([study, *form.cleaned_data['members']]))
    if not course.modules.filter(program=study.program_id).exists():
        messages.error(request, 'Diese Veranstaltung ist in Ihrem Studiengang nicht anrechenbar.')
        return redirect(course)
    with transaction.atomic():
        group = Ensemble.objects.create(course=course, name=form.cleaned_data['name'], repertoire=form.cleaned_data['repertoire'], wish=form.cleaned_data['wish'])
        group.members.set(members)
        for s in members:
            Participation.objects.get_or_create(course=course, study=s, defaults={'module': course.modules.filter(program=s.program_id).order_by('semester').first()})
    for s in members[1:]:
        texts.send('ensemble', s.person, course.get_absolute_url(), veranstaltung=course.title, besetzung=group.name, von=request.user)
    messages.success(request, f'„{group.name}“ angemeldet. Die Betreuung teilen die Lehrenden zu.')
    return redirect(course)


@require_POST
def coach(request, pk):
    """Give an ensemble its coach, or take it back; the members hear who it is."""
    group = get_object_or_404(Ensemble.objects.select_related('course__term'), pk=pk)
    course = group.course
    if not teaches(request.user, course):
        raise PermissionDenied
    if 'dissolve' in request.POST:
        group.delete()
        messages.success(request, f'„{group.name}“ aufgelöst. Die Teilnahmen bleiben.')
        return redirect(course)
    group.coach = course.staff.filter(pk=request.POST.get('coach') or None).first()
    group.save(update_fields=['coach'])
    if group.coach:
        for s in group.members.select_related('person'):
            texts.send('betreuung', s.person, course.get_absolute_url(), veranstaltung=course.title, besetzung=group.name, betreuung=group.coach)
    messages.success(request, f'„{group.name}“: {group.coach or "ohne Betreuung"}.')
    return redirect(course)


@require_POST
def choose(request, pk):
    """Accept a wish for a place, or decline it; either way the student hears."""
    signup = get_object_or_404(Signup.objects.select_related('course__term', 'study__person'), pk=pk)
    course, person = signup.course, signup.study.person
    if not teaches(request.user, course):
        raise PermissionDenied
    if request.POST.get('decision') == 'yes':
        Participation.objects.get_or_create(course=course, study=signup.study, module=signup.module)
        texts.send('eingetragen', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    else:
        texts.send('kein_platz', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    signup.delete()
    messages.success(request, f'{person}: {"zugelassen" if request.POST.get("decision") == "yes" else "abgesagt"}. Nachricht versandt.')
    return redirect(course)


@require_POST
def retract(request, pk):
    signup = get_object_or_404(Signup, pk=pk, study__person=request.user)
    signup.delete()
    messages.success(request, 'Anfrage zurückgezogen.')
    return redirect(signup.course)


@require_POST
def date(request, pk):
    """Book one date of a course in a room: a block seminar day, a rehearsal, a make-up lesson."""
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    form = DateForm(request.POST, person=request.user)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect(course)
    data = form.cleaned_data
    start = timezone.make_aware(datetime.combine(data['day'], data['begin']))
    booking = Booking(
        room=data['room'], start=start, end=timezone.make_aware(datetime.combine(data['day'], data['until'])),
        person=request.user, course=course, category=BookingCategory.system('unterricht'),
    )
    try:
        booking.full_clean()
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        booking.save()
        messages.success(request, f'Termin am {start:%d.%m.%Y} in {booking.room} gebucht.')
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
    bookings, lesson, free = [], BookingCategory.system('unterricht'), Holiday.days(term.lectures_start, term.lectures_end)
    for day in (first + timedelta(weeks=n) for n in range((term.lectures_end - first).days // 7 + 1)):
        if day in free:
            continue
        start = timezone.make_aware(datetime.combine(day, data['time']))
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
            load=deputat(teaching, person),
            coached=person.coaching.filter(course__term=term).select_related('course').prefetch_related('members__person'),
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
    courses = term.courses.filter(kind__in=TEACHING).prefetch_related('staff', 'ensembles').annotate(size=Count('participations'))
    everyone = 'view_teaching_load' in person.roles
    teaching = Person.objects.filter(teaching__term=term, teaching__kind__in=TEACHING)
    seen = None if person.everywhere('view_teaching_load') else {person.pk, *person.within(teaching, 'view_teaching_load').values_list('pk', flat=True)}  # own load, and that of the Institute in reach
    if seen is not None:
        courses = courses.filter(pk__in=Course.objects.filter(staff__in=seen).values('pk'))
    institute, q = request.GET.get('institut', ''), request.GET.get('q', '').strip()
    words = fold(q).split()
    teachers = defaultdict(list)
    for c in courses:
        coached = Counter(e.coach_id for e in c.ensembles.all())
        for s in c.staff.all():
            if (seen is None or s.pk in seen) and institute in ('', str(s.institute_id)) and all(w in fold(s.name) for w in words):
                teachers[s].append((c, (c.hours or 0) * share(c, coached[s.pk])))
    rows = sorted(((t, cs, sum(load for _, load in cs)) for t, cs in teachers.items()), key=lambda row: row[0].last_name)
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
                    term=target, kind=c.kind, title=c.title, area=c.area, format=c.format, subject=c.subject, hours=c.hours,
                    capacity=c.capacity, allocation=c.allocation, description=c.description,
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
