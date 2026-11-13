"""Lehraufträge: proposed when a Lehrbeauftragte:r is put on a course, granted in writing, the hours held confirmed after
the term by the person and the office, for the payroll (MACH); and the Lehrverpflichtungen taken into the next term."""
import csv
from collections import Counter, defaultdict
from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_POST

from core import history, texts
from core.access import require
from core.apps import plural
from core.models import Export, Office, Person, Term
from core.shortcuts import without_term
from rooms.models import Booking
from ..models import Assignment, Obligation, per_sws
from .common import TEACHING
from .load import deputat, hours


def propose(term, by, people=None):
    """Lehraufträge for the Lehrbeauftragte teaching in the term (of `people`, else all): a proposal where there is none
    or it was withdrawn, at the SWS they teach, with their courses. One granted keeps its SWS as agreed and gains the
    courses added; the page then shows it teaches more, to grant again."""
    courses = term.courses.filter(kind__in=TEACHING, cancelled=None)
    teachers = Person.objects.filter(employment=Person.Employment.LEHRAUFTRAG, teaching__in=courses).distinct()
    if people is not None:
        teachers = teachers.filter(pk__in=[p.pk for p in people])
    for teacher in teachers:
        theirs = list(courses.filter(staff=teacher))
        assignment = Assignment.objects.filter(person=teacher, term=term).first() or Assignment(person=teacher, term=term)
        if assignment.status == Assignment.Status.GRANTED:
            assignment.courses.add(*theirs)
            continue
        assignment.status, assignment.hours, assignment.proposed_by = Assignment.Status.PROPOSED, agreed(courses.filter(staff=teacher), teacher), by
        assignment.save()
        assignment.courses.set(theirs)


def agreed(courses, teacher):
    """The SWS a Lehrauftrag agrees for these courses: what the Deputat counts them as, to a tenth."""
    return Decimal(deputat(courses, teacher)).quantize(Decimal('0.1'))


def held(assignments):
    """{pk: LVS} each Lehrauftrag's hours held: the dates of its courses gone by and not called off that its person
    teaches, a course's own and their groups' and ensembles', each by the minutes its Lehrform counts to an LVS (per_sws)."""
    taking = defaultdict(list)  # course → the Lehraufträge it is on
    for a in assignments:
        for c in a.courses.all():
            taking[c.pk].append(a)
    found = Counter()
    dates = Booking.objects.filter(course__in=taking, end__lte=timezone.now()).exclude(category='abgesagt').select_related('course__format', 'ensemble').prefetch_related('group__staff')
    for b in dates:
        lvs = Decimal(int((b.end - b.start).total_seconds()) // 60) / per_sws(b.course.format.short if b.course.format_id else '')
        for a in taking[b.course_id]:
            if b.group_id and a.person_id not in {s.pk for s in b.group.staff.all()} or b.ensemble_id and b.ensemble.coach_id != a.person_id:
                continue
            found[a.pk] += lvs
    return {pk: hours.quantize(Decimal('0.1')) for pk, hours in found.items()}


@require('grant_assignments', 'view_teaching_load')
def assignments(request):
    """A term's Lehraufträge as the office granting them keeps them: each with its courses, the SWS agreed and taught, where
    it stands, and after the lectures the hours held. Whoever sees the Deputat reads those in their reach."""
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term is None:
        return without_term(request, 'ein Semester mit Lehraufträgen')
    granting = 'grant_assignments' in person.roles
    rows = person.within(Assignment.objects.filter(term=term), 'grant_assignments' if granting else 'view_teaching_load')
    stand = request.GET.get('stand', '')
    if stand in Assignment.Status.values:
        rows = rows.filter(status=stand)
    rows = list(rows.select_related('person__institute', 'granted_by', 'settled_by').prefetch_related('courses').order_by('person__last_name', 'person__first_name'))
    teaching = hours(term, {a.person_id for a in rows})
    over = timezone.localdate() > term.lectures_end
    done = held([a for a in rows if not a.settled_on]) if over else {}
    for a in rows:
        a.teaching = sum(h for _, h in teaching.get(a.person, []))
        a.more = a.status == Assignment.Status.GRANTED and a.teaching > a.hours  # teaches more than agreed: grant again
        a.so_far = a.held if a.settled_on else done.get(a.pk, Decimal(0)) if over else None
    return render(request, 'courses/assignments.html', {
        'term': term, 'terms': Term.objects.filter(assignments__isnull=False).distinct().order_by('-start'), 'rows': rows, 'stand': stand,
        'statuses': Assignment.Status.choices, 'granting': granting, 'over': over,
    })


def chosen(request):
    return request.user.within(Assignment.objects.filter(pk__in=request.POST.getlist('auftrag')), 'grant_assignments').select_related('person', 'term')


def back(request):
    return redirect(f"{reverse('courses:assignments')}?semester={request.POST.get('semester', '')}")


@require('grant_assignments')
@require_POST
def grant(request):
    """Grant the Lehraufträge ticked at what each teaches now, with those courses; the letter goes to the person. Granting
    one again agrees the SWS and courses it has come to."""
    granted = 0
    for a in chosen(request).exclude(status=Assignment.Status.WITHDRAWN):
        courses = list(a.term.courses.filter(kind__in=TEACHING, cancelled=None, staff=a.person))
        if not courses:
            continue
        a.status, a.granted_by, a.granted = Assignment.Status.GRANTED, request.user, timezone.localdate()
        a.hours = agreed(a.term.courses.filter(pk__in=[c.pk for c in courses]), a.person)
        a.save()
        a.courses.set(courses)
        texts.send(
            'lehrauftrag', a.person, reverse('courses:mine'), semester=a.term, umfang=f'{number_format(a.hours)} SWS' if a.hours else 'nach gehaltenen Stunden',
            veranstaltungen=', '.join(sorted(c.title for c in courses)), von=request.user,
        )
        granted += 1
    messages.success(request, f'{plural(granted, "Lehrauftrag", "Lehraufträge")} erteilt; die Bescheide sind im Postfach der Lehrbeauftragten.' if granted else 'Nichts erteilt: ohne Lehrveranstaltung kein Lehrauftrag.')
    return back(request)


@require('grant_assignments')
@require_POST
def withdraw(request, pk):
    """Withdraw a Lehrauftrag, for a reason the person is told; putting them on a course again proposes a new one."""
    a = get_object_or_404(request.user.within(Assignment.objects.select_related('person', 'term'), 'grant_assignments'), pk=pk)
    if not (reason := request.POST.get('grund', '').strip()[:200]):
        messages.error(request, 'Bitte den Grund angeben; er steht im Bescheid an die Person.')
        return back(request)
    was = a.status
    a.status, a.note = Assignment.Status.WITHDRAWN, reason
    a.save()
    if was == Assignment.Status.GRANTED:
        texts.send('lehrauftrag_zurueckgezogen', a.person, reverse('courses:mine'), semester=a.term, grund=reason, von=request.user)
    messages.success(request, f'Lehrauftrag {a.person} zurückgezogen.')
    return back(request)


@require('grant_assignments')
@require_POST
def settle(request):
    """Confirm the hours held of the Lehraufträge ticked, once the lectures are over: the figure the payroll gets."""
    rows = [a for a in chosen(request).filter(status=Assignment.Status.GRANTED, settled_on=None).prefetch_related('courses') if timezone.localdate() > a.term.lectures_end]
    done = held(rows)
    for a in rows:
        a.held, a.settled_by, a.settled_on = done.get(a.pk, Decimal(0)), request.user, timezone.localdate()
        a.save()
    messages.success(request, f'Stunden von {plural(len(rows), "Lehrauftrag", "Lehraufträgen")} bestätigt.' if rows else 'Nichts zu bestätigen: erst nach der Vorlesungszeit.')
    return back(request)


@require('grant_assignments')
def export(request):
    """A term's granted Lehraufträge as CSV for the payroll: agreed and held, and who confirmed them."""
    term = get_object_or_404(Term, pk=request.GET.get('semester') or None)
    rows = request.user.within(term.assignments.filter(status=Assignment.Status.GRANTED), 'grant_assignments').select_related('person__institute', 'settled_by').prefetch_related('courses')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="lehrauftraege-{term.pk}.csv"'
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Nachname', 'Vorname', 'Institut', 'Lehrveranstaltungen', 'Vereinbart (SWS)', 'Erteilt am', 'Gehalten (LVS)', 'Von der Person bestätigt', 'Bestätigt von', 'Bestätigt am'])
    rows = list(rows.order_by('person__last_name', 'person__first_name'))
    Export.log(request.user, f'Lehraufträge {term}', len(rows))
    for a in rows:
        writer.writerow([
            a.person.last_name, a.person.first_name, a.person.institute or '', ', '.join(sorted(c.title for c in a.courses.all())), number_format(a.hours),
            f'{a.granted:%d.%m.%Y}' if a.granted else '', '' if a.held is None else number_format(a.held),
            f'{timezone.localtime(a.affirmed):%d.%m.%Y}' if a.affirmed else '', a.settled_by or '', f'{a.settled_on:%d.%m.%Y}' if a.settled_on else '',
        ])
    return response


@require_POST
def affirm(request, pk):
    """A Lehrbeauftragte:r confirms the dates held of their granted Lehrauftrag, once the lectures are over."""
    a = get_object_or_404(Assignment.objects.select_related('term'), pk=pk, person=request.user, status=Assignment.Status.GRANTED, affirmed=None)
    if timezone.localdate() <= a.term.lectures_end:
        messages.error(request, 'Bestätigen lässt sich nach dem Ende der Vorlesungszeit.')
    else:
        a.affirmed = timezone.now()
        a.save(update_fields=['affirmed'])
        messages.success(request, f'Gehaltene Stunden bestätigt; {Office.holding("grant_assignments") or "das zuständige Amt"} gibt sie zur Vergütung weiter.')
    return redirect(f"{reverse('courses:mine')}?semester={a.term_id}")


@require('manage_staff')
@require_POST
def obligations_again(request):
    """This term's Lehrverpflichtungen into the next for whoever is still active and has none there yet, with their
    Grundlage; the office then changes what differs."""
    now, target = Term.current(), Term.upcoming()
    if not (now and target):
        messages.error(request, 'Erst wenn das nächste Semester angelegt ist.')
        return redirect('setup:list', 'lehrverpflichtungen')
    taken = target.obligations.values('person')
    made = [
        Obligation(person_id=o.person_id, term=target, hours=o.hours, basis=o.basis)
        for o in request.user.within(now.obligations.filter(person__is_active=True).exclude(person__in=taken), 'manage_staff')
    ]
    history.saved(Obligation.objects.bulk_create(made))
    messages.success(request, f'{plural(len(made), "Lehrverpflichtung", "Lehrverpflichtungen")} aus dem {now} ins {target} übernommen.' if made else 'Nichts zu übernehmen.')
    return redirect('setup:list', 'lehrverpflichtungen')
