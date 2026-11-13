"""The Semesterwechsel: carrying courses, examiners and auditions into the next term."""
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import history, texts
from core.access import require
from core.apps import plural
from core.dates import day as understood
from core.models import Institute, Office, Person, Term
from core.shortcuts import without_term
from core.text import matching
from curriculum.models import Module, Program
from rooms.models import Booking
from students.models import Study
from ..models import Appointment, Area, Course, Group, Participation, Reduction
from .assignments import propose
from .common import TEACHING
from .dates import weekly
from .load import loads


def missing(target):
    """What `target` may take over, each course by its Turnus: lesson classes and what runs every semester from the term
    before, what runs yearly from the same semester a year before, one-off courses from either, offered again only by
    choice; nothing `target` has already."""
    before = Term.objects.filter(start__lt=target.start).order_by('-start').first()
    year = Term.objects.filter(start__lte=target.start - timedelta(days=365)).order_by('-start').first()
    every = Q(kind=Course.Kind.LESSON) | Q(rhythm=Course.Rhythm.SEMESTER)
    return Course.objects.filter(kind__in=TEACHING).filter(
        Q(term=before) & (every | Q(rhythm=Course.Rhythm.ONCE)) | Q(term=year, kind=Course.Kind.COURSE) & ~Q(rhythm=Course.Rhythm.SEMESTER),
    ).exclude(title__in=target.courses.values('title'))


WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']


def usual(courses, by='course'):
    """{course pk: (weekday, time, minutes, room)}: each course's weekly date as it mostly took place, its groups' left
    out; by='group', each group's of these."""
    found = defaultdict(Counter)
    dates = Booking.objects.filter(**{f'{by}__in': courses}, ensemble=None, category__blocks=True)
    for b in (dates.filter(group=None) if by == 'course' else dates).select_related('room'):
        start = timezone.localtime(b.start)
        found[getattr(b, f'{by}_id')][start.weekday(), start.time(), int((b.end - b.start).total_seconds() // 60), b.room] += 1
    return {pk: seen.most_common(1)[0][0] for pk, seen in found.items()}


def described(slot):
    """A weekly date (usual) as people read it: 'Di 10:00, 90 Min. · FH 311'."""
    return slot and f'{WEEKDAYS[slot[0]]} {slot[1]:%H:%M}, {slot[2]} Min. · {slot[3]}'


def gaps(target):
    """Course work of a Studienplan that everyone on it takes in the Fachsemester they reach in `target`, taught as a
    course before, that no course of `target` offers: [(module, students)], the most students first."""
    today = timezone.localdate()
    reach = Counter(
        Study.objects.filter(status=Study.Status.ENROLLED)
        .annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__withdrawn=False, terms__term__start__lte=today)))
        .values_list('program', 'fs')
    )
    taught = set(Module.objects.filter(courses__kind__in=TEACHING).values_list('pk', flat=True))
    offered = set(Module.objects.filter(courses__term=target, courses__cancelled=None).values_list('pk', flat=True))
    found = []

    def walk(node, program):
        if node.nodes and node.credits < node.offered:  # a choice: each option only some take
            return
        if not node.nodes and node.pk in taught and node.pk not in offered and (n := reach[program.pk, (node.semester or 0) - 1]):
            found.append((node, n))
        for kid in node.nodes:
            walk(kid, program)

    for program in Program.objects.filter(pk__in={program for program, _ in reach}):
        for root in program.curriculum():
            walk(root, program)
    return sorted(found, key=lambda f: -f[1])


@require('roll_over', 'publish_term')
def rollover(request):
    """The next semester planned in steps, unseen by students until it is published: take its courses over as drafts, ask
    the teachers to confirm them, book last year's weekly times, fill the classes after the Rückmeldung, check, publish."""
    target = Term.upcoming()
    if target is None:
        return without_term(request, 'das nächste Semester, in das die Lehrveranstaltungen übernommen werden')
    person = request.user
    pending = person.within(missing(target), 'roll_over')
    if request.method == 'POST':
        step = {'uebernehmen': take_over, 'fragen': ask, 'termine': schedule, 'klassen': fill, 'veroeffentlichen': publish, 'ermaessigungen': carry}.get(request.POST.get('schritt'))
        if step is None or {publish: 'publish_term', carry: 'manage_teaching_load'}.get(step, 'roll_over') not in person.roles:
            raise PermissionDenied
        step(request, target, pending)
        return redirect('courses:rollover')
    institute, area, kind, q = (request.GET.get(name, '').strip() for name in ('institut', 'fachgebiet', 'art', 'q'))
    if institute.isdigit():
        pending = pending.filter(Q(subject__institute=institute) | Q(staff__institute=institute))
    if area.isdigit():
        pending = pending.filter(area=area)
    if kind in TEACHING:
        pending = pending.filter(kind=kind)
    if q:
        pending = matching(pending, q, 'title', 'staff__last_name')
    ours, rows, titles = person.ours('roll_over'), [], set()
    for c in pending.distinct().select_related('term', 'area', 'subject__institute').prefetch_related('staff__institute').order_by('-term__start', 'title'):
        if c.title not in titles:  # the newest offering once
            titles.add(c.title)
            c.others = sorted({str(s.institute) for s in c.staff.all() if s.institute_id and s.institute_id not in ours}) if ours else []
            c.group = f'Einzelunterricht · {c.subject.institute}' if c.kind == Course.Kind.LESSON and c.subject and c.subject.institute else c.area.name if c.area else 'Ohne Fachgebiet'
            rows.append(c)
    slots = usual([c.pk for c in rows if c.kind == Course.Kind.COURSE])
    for c in rows:
        c.slot = described(slots.get(c.pk))
    rows.sort(key=lambda c: (c.kind == Course.Kind.LESSON, c.group, c.title))
    planned = target.courses.filter(kind__in=TEACHING).exclude(origin=None)  # taken over: for their teachers to confirm
    live = planned.filter(cancelled=None)
    waiting = live.filter(confirmed=None)
    timeless = list(target.courses.filter(kind=Course.Kind.COURSE, cancelled=None, bookings__isnull=True).select_related('origin').order_by('title'))
    slots = usual([c.origin_id for c in timeless if c.origin_id])
    for c in timeless:
        c.slot = described(slots.get(c.origin_id))
    load = loads(target, person) if 'view_teaching_load' in person.roles else []
    staffed = Person.objects.filter(teaching__in=live).exclude(employment=Person.Employment.LEHRAUFTRAG).distinct()
    return render(request, 'courses/rollover.html', {
        'target': target, 'rows': rows, 'planner': 'roll_over' in person.roles, 'publisher': 'publish_term' in person.roles, 'creating': 'manage_courses' in person.roles,
        'institutes': Institute.objects.all(), 'areas': Area.objects.all(), 'institute': institute, 'area': area, 'kind': kind, 'q': q,
        'planned': planned.count(), 'confirmed': live.exclude(confirmed=None).count(), 'dropped': planned.exclude(cancelled=None).select_related('origin'),
        'waiting': Person.objects.filter(teaching__in=waiting).annotate(n=Count('teaching', filter=Q(teaching__in=waiting))).order_by('last_name'),
        'timeless': timeless, 'fillable': timezone.localdate() > target.deadline,
        'over': [row for row in load if row.balance and row.balance > 0], 'under': [row for row in load if row.low],
        'assignments': Counter(target.assignments.values_list('status', flat=True)),
        'unbound': staffed.exclude(obligations__term__start__lte=target.start).count(), 'staffing': 'manage_staff' in person.roles,  # teaching there without a Lehrverpflichtung
        'leave': Reduction.objects.filter(term=target, ground__leave=True, person__teaching__in=live).distinct().select_related('person', 'ground'),
        'gaps': gaps(target)[:12] if planned.exists() else [],
        'publishers': Office.holding('publish_term', linked=True),
        'reductions': (target.reductions.count(), Term.current() and Term.current().reductions.exclude(ground__leave=True).count()),
        'reducing': 'manage_teaching_load' in person.roles,
        'proposed': target.ready_by and max(timezone.localdate() + timedelta(weeks=1), target.ready_by - timedelta(weeks=2)),  # time to answer, and to check after 'unpublishers': Office.holding('manage_terms'),
    })


def predecessor(course):
    """The course of the semester before whose groups a course continues: one taught in groups that counts for a module
    the course's own modules require, as Gehörbildung 1 for Gehörbildung 2."""
    before = Term.objects.filter(start__lt=course.term.start).order_by('-start').first()
    return Course.objects.filter(term=before, kind=Course.Kind.COURSE, modules__required_by__courses=course, groups__isnull=False).distinct().first()


def take_over(request, target, pending):
    """The ticked courses as drafts of `target`, from what they were: staff, Turnus, and the modules they counted for, also
    as the same semester a year before had them; registration as the semester sets it; lesson classes without their
    students, who come once they have re-registered (fill)."""
    chosen, titles = [], set()
    for c in pending.filter(pk__in=request.POST.getlist('course')).prefetch_related('modules', 'staff', 'groups__staff').order_by('-term__start'):
        if c.title not in titles:
            titles.add(c.title)
            chosen.append(c)
    year = Term.objects.filter(start__lte=target.start - timedelta(days=365)).order_by('-start').first()
    earlier = defaultdict(set)  # what runs every semester may count for other semesters' modules in summer and in winter
    for title, module in Course.objects.filter(term=year, title__in=titles, kind__in=TEACHING).values_list('title', 'modules'):
        if module:
            earlier[title].add(module)
    with transaction.atomic():
        for c in chosen:
            copy = Course.objects.create(
                term=target, kind=c.kind, title=c.title, area=c.area, format=c.format, subject=c.subject, hours=c.hours, capacity=c.capacity,
                allocation=c.allocation, grouping=c.grouping, description=c.description, rhythm=c.rhythm, origin=c,
                in_moodle=c.in_moodle, copy_content=c.copy_content, opens=target.course_signup_start, deadline=target.course_signup_end,
            )
            copy.modules.set({m.pk for m in c.modules.all()} | earlier[c.title])
            copy.staff.set(c.staff.all())
            source = predecessor(copy) if c.grouping == Course.Grouping.CONTINUED else c  # groups that stay together, with their teachers
            for g in source.groups.prefetch_related('staff') if source else []:
                Group.objects.create(course=copy, number=g.number, capacity=g.capacity).staff.set(g.staff.all())
                copy.staff.add(*g.staff.all())
        propose(target, request.user)  # the Lehraufträge renewed: proposed, for the office granting them to grant
    hidden = ' als Entwurf; Studierende sehen sie, sobald das Semester veröffentlicht ist' if target.planning else ''
    messages.success(request, f'{plural(len(chosen), "Lehrveranstaltung", "Lehrveranstaltungen")} nach {target} übernommen{hidden}.')


def ask(request, target, pending):
    """Ask every teacher with a course still unconfirmed to confirm, change or call it off by a day; asked again, those
    still open."""
    by = understood(request.POST.get('bis', ''))
    if by is None or by <= timezone.localdate():
        messages.error(request, 'Bitte einen Tag in der Zukunft angeben, bis zu dem die Lehrenden antworten.')
        return
    if target.ready_by and by >= target.ready_by:
        messages.error(request, f'Die Antworten braucht es vor dem {date_format(target.ready_by, "j.n.Y")}, wenn das Vorlesungsverzeichnis fertig sein soll.')
        return
    target.replies_by = by
    target.save(update_fields=['replies_by'])
    waiting = target.courses.filter(kind__in=TEACHING, confirmed=None, cancelled=None).exclude(origin=None)
    teachers = list(Person.objects.filter(teaching__in=waiting).distinct().prefetch_related(Prefetch('teaching', waiting, to_attr='open')))
    for t in teachers:
        texts.send(
            'semesterplanung', t, f"{reverse('courses:mine')}?semester={target.pk}", semester=str(target), frist=date_format(by, 'j. F Y'),
            veranstaltungen='\n'.join(f'– {c.title}' for c in t.open),
        )
    messages.success(request, f'{plural(len(teachers), "Lehrperson", "Lehrpersonen")} gefragt, Antwort bis zum {date_format(by, "j.n.Y")}.')


def schedule(request, target, pending):
    """Book the ticked drafts weekly as their course took place last time, where the room is free on every date."""
    courses = list(target.courses.filter(pk__in=request.POST.getlist('course'), kind=Course.Kind.COURSE, cancelled=None, bookings__isnull=True).exclude(origin=None).prefetch_related('groups__staff'))
    slots, booked, taken = usual([c.origin_id for c in courses]), 0, []
    source = {c.pk: (p.pk if c.grouping == Course.Grouping.CONTINUED and (p := predecessor(c)) else c.origin_id) for c in courses}  # where its groups met
    before = {(g.course_id, g.number): g.pk for g in Group.objects.filter(course__in=source.values())}
    times = usual(before.values(), 'group')
    for c in courses:
        if groups := c.groups.all():  # each group at its time before, booked by its teacher
            dates = [
                b for g in groups if (slot := times.get(before.get((source[c.pk], g.number))))
                for b in weekly(c, g.staff.first() or request.user, dict(zip(['weekday', 'time', 'minutes', 'room'], slot), group=g))
            ]
        elif (slot := slots.get(c.origin_id)) is None:
            continue
        else:
            weekday, time, minutes, room = slot
            dates = weekly(c, request.user, {'weekday': weekday, 'time': time, 'minutes': minutes, 'room': room})
        if not dates:
            continue
        busy = Booking.objects.filter(room__in={b.room_id for b in dates}, category__blocks=True, start__lt=max(b.end for b in dates), end__gt=min(b.start for b in dates)).values_list('room', 'start', 'end')
        if clashing := {str(b.room) for r, s, e in busy for b in dates if r == b.room_id and s < b.end and b.start < e}:
            taken.append(f'{c.title} ({", ".join(sorted(clashing))})')
            continue
        history.saved(Booking.objects.bulk_create(dates))
        booked += 1
    messages.success(request, f'{plural(booked, "Lehrveranstaltung hat", "Lehrveranstaltungen haben")} ihre wöchentlichen Termine.')
    if taken:
        messages.error(request, f'Raum belegt, nicht gebucht: {"; ".join(taken)}. Diese bitte auf ihrer Seite planen.')


def fill(request, target, pending):
    """After the Rückmeldefrist: each lesson class of `target` gets whoever was in it and re-registered, not on leave, and
    the new students of its teachers in its Hauptfach; each with the module of their next Fachsemester. Each group that
    stays together gets whoever was in it the semester before and did not fail."""
    if timezone.localdate() <= target.deadline:
        messages.error(request, f'Erst nach der Rückmeldefrist am {date_format(target.deadline, "j.n.Y")}.')
        return
    today = timezone.localdate()
    studies = Study.objects.filter(status=Study.Status.ENROLLED, terms__term=target, terms__leave=False, terms__withdrawn=False).annotate(
        fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__withdrawn=False, terms__term__start__lte=today)),
    ).in_bulk()
    added = 0
    for c in target.courses.filter(kind=Course.Kind.LESSON, cancelled=None).prefetch_related('modules', 'staff'):
        staff = {p.pk for p in c.staff.all()}
        had = set(Participation.objects.filter(course=c.origin_id).values_list('study', flat=True)) if c.origin_id else set()
        new = {s.pk for s in studies.values() if s.start_term_id == target.pk and s.teacher_id in staff and s.subject_id == c.subject_id}
        rows = []
        for pk in (had | new) & (studies.keys() - set(c.participations.values_list('study', flat=True))):
            s = studies[pk]
            plan = [m for m in c.modules.all() if m.program_id == s.program_id]
            module = next((m for m in plan if m.semester == s.fs + 1), None) or min(plan, key=lambda m: m.semester or 99, default=None)
            rows.append(Participation(course=c, study=s, module=module))
        history.saved(Participation.objects.bulk_create(rows))
        added += len(rows)
    for c in target.courses.filter(kind=Course.Kind.COURSE, cancelled=None, grouping=Course.Grouping.CONTINUED).prefetch_related('groups', 'modules'):
        if (p := predecessor(c)) is None:
            continue
        taken, rows = set(c.participations.values_list('study', flat=True)), []
        was = {g.number: set(g.participations.exclude(passed=False).values_list('study', flat=True)) for g in p.groups.all()}
        for g in c.groups.all():
            for pk in was.get(g.number, set()) & (studies.keys() - taken):
                s, taken = studies[pk], taken | {pk}
                rows.append(Participation(course=c, study=s, group=g, module=min((m for m in c.modules.all() if m.program_id == s.program_id), key=lambda m: m.semester or 99, default=None)))
        history.saved(Participation.objects.bulk_create(rows))
        added += len(rows)
    messages.success(request, f'{plural(added, "Person", "Personen")} in die Klassen und die fortgeführten Gruppen des {target} eingetragen.')


@require('appoint_examiners')
@require_POST
def appoint_again(request):
    """The latest Studienjahr's appointments into the next, for whoever is still active and not appointed there yet;
    each in the same Hauptfächer. The Prüfungsausschuss then changes what differs."""
    last = Appointment.objects.order_by('-year').values_list('year', flat=True).first()
    if last is None:
        messages.error(request, 'Noch keine Bestellungen, die sich übernehmen ließen.')
        return redirect('setup:list', 'pruefende')
    taken = set(Appointment.objects.filter(year=last + 1).values_list('person', flat=True))
    made = []
    with transaction.atomic():
        for a in Appointment.objects.filter(year=last, person__is_active=True).exclude(person__in=taken).prefetch_related('subjects'):
            made.append(Appointment.objects.create(person_id=a.person_id, year=last + 1, general=a.general))
            made[-1].subjects.set(a.subjects.all())
    messages.success(request, f'{plural(len(made), "Bestellung", "Bestellungen")} aus {last}/{(last + 1) % 100:02d} für {last + 1}/{(last + 2) % 100:02d} übernommen.')
    return redirect('setup:list', 'pruefende')


@require('run_auditions')
@require_POST
def auditions_again(request):
    """Each intake into the same semester a year on, for a semester to come that has no Eignungsprüfung yet: every stage
    the office keeps, with its jury, its Fristen a year on by whole weeks, the stages linked as before and to the new
    semester's Korrepetition. Rooms and times are planned anew."""
    made = []
    with transaction.atomic():
        for target in Term.objects.filter(start__gt=timezone.localdate()).exclude(courses__kind=Course.Kind.AUDITION).order_by('start'):
            source = Term.objects.filter(start__range=(target.start - timedelta(days=400), target.start - timedelta(days=330)), courses__kind=Course.Kind.AUDITION).distinct().first()
            if source:
                made.append((source, target, copy_auditions(request.user, source, target)))
    if not made:
        messages.error(request, 'Kein Semester ohne Eignungsprüfungen, das ein Jahr nach einem mit Eignungsprüfungen beginnt: bitte erst das Semester anlegen.')
    for source, target, n in made:
        messages.success(request, f'{plural(n, "Eignungsprüfung", "Eignungsprüfungen")} aus {source} nach {target} übernommen.')
    return redirect('setup:list', 'eignungspruefungen')


def copy_auditions(person, source, target):
    """The auditions of `source` the person keeps, copied into `target`; returns how many."""
    shift = timedelta(weeks=round((target.start - source.start).days / 7))
    versions = {(p.degree, p.name): p for p in Program.current(target)}  # each Studiengang in the PO-Version that applies then
    old = list(person.within(Course.objects.filter(term=source, kind=Course.Kind.AUDITION), 'run_auditions').select_related('accompaniment').prefetch_related('staff', 'after'))
    new = {}
    for c in old:
        new[c.pk] = Course.objects.create(
            term=target, kind=c.kind, title=c.title, area=c.area, format=c.format, subject=c.subject, oral=c.oral, hours=c.hours,
            program=c.program and versions.get((c.program.degree, c.program.name), c.program),
            capacity=c.capacity, allocation=c.allocation, pass_mark=c.pass_mark, max_points=c.max_points, video=c.video, description=c.description,
            rhythm=c.rhythm, origin=c, opens=c.opens and c.opens + shift, deadline=c.deadline and c.deadline + shift, marks_by=c.marks_by and c.marks_by + shift,
            accompaniment=c.accompaniment and Course.objects.filter(term=target, kind=c.accompaniment.kind, title=c.accompaniment.title).first(),
        )
        new[c.pk].staff.set(c.staff.all())
    for c in old:
        new[c.pk].after.set(new[a.pk] for a in c.after.all() if a.pk in new)
    return len(new)


def carry(request, target, pending):
    """Carry this semester's Ermäßigungen over to the next for whoever has none there yet; a Freistellung is for its
    semester alone."""
    now, taken = Term.current(), set(target.reductions.values_list('person', flat=True))
    made = [
        Reduction(person_id=r.person_id, term=target, ground_id=r.ground_id, hours=r.hours, note=r.note)
        for r in now.reductions.exclude(ground__leave=True).exclude(person__in=taken)
    ] if now else []
    history.saved(Reduction.objects.bulk_create(made))
    messages.success(request, f'{plural(len(made), "Ermäßigung", "Ermäßigungen")} aus dem {now} übernommen.' if made else 'Nichts zu übernehmen.')


def publish(request, target, pending):
    """Publish `target`: students see its courses; those its teachers called off, never offered, are deleted."""
    dropped = target.courses.filter(kind__in=TEACHING, cancelled__isnull=False, participations=None)
    with transaction.atomic():
        n = dropped.count()
        dropped.delete()
        target.planning = False
        target.save(update_fields=['planning'])
    messages.success(request, f'{target} veröffentlicht: Studierende sehen seine Lehrveranstaltungen.' + (f' {plural(n, "abgesagte Lehrveranstaltung", "abgesagte Lehrveranstaltungen")} gelöscht.' if n else ''))
