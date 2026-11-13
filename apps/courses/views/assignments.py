"""Lehraufträge: proposed when a Lehrbeauftragte:r is put on a course, granted in writing at a Vergütungsstufe, accepted
by the person, the hours held confirmed after the term by the person and the office, for the payroll (MACH)."""
import csv
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.views.decorators.http import require_POST

from core import texts
from core.access import require
from core.apps import plural
from core.models import Export, Holiday, Institute, Institution, Office, Person, Term
from core.shortcuts import without_term
from core.text import matching
from rooms.models import Booking
from ..models import Assignment, Group, Rate, per_sws
from .common import TEACHING
from .load import deputat, hours

GRANTED, PROPOSED, WITHDRAWN = Assignment.Status.GRANTED, Assignment.Status.PROPOSED, Assignment.Status.WITHDRAWN


def propose(term, by, people=None):
    """Lehraufträge for the Lehrbeauftragte teaching in the term (of `people`, else all): a proposal where there is none,
    at the SWS they teach, with their courses; returns whom it was made for. A granted one keeps its SWS as agreed and
    gains the courses added, to be granted again; a withdrawn one stays so, its page saying whom they still teach."""
    courses = term.courses.filter(kind__in=TEACHING, cancelled=None)
    teachers = Person.objects.filter(employment=Person.Employment.LEHRAUFTRAG, teaching__in=courses).distinct()
    if people is not None:
        teachers = teachers.filter(pk__in=[p.pk for p in people])
    made = []
    for teacher in teachers:
        theirs = list(courses.filter(staff=teacher))
        assignment = Assignment.objects.filter(person=teacher, term=term).first() or Assignment(person=teacher, term=term, proposed_by=by)
        if assignment.status == WITHDRAWN:
            continue
        if assignment.status == GRANTED:
            assignment.courses.add(*theirs)
            continue
        if assignment.pk is None:
            made.append(teacher)
        assignment.hours = agreed(courses.filter(staff=teacher), teacher)
        assignment.save()
        assignment.courses.set(theirs)
    return made


def agreed(courses, teacher):
    """The SWS a Lehrauftrag agrees for these courses: what the Deputat counts them as, to a tenth."""
    return Decimal(deputat(courses, teacher)).quantize(Decimal('0.1'))


def weeks(term):
    """The teaching weeks of a term's lecture period: those with a weekday free of holidays."""
    off = Holiday.days(term.lectures_start, term.lectures_end)
    days = (term.lectures_start + timedelta(days=n) for n in range((term.lectures_end - term.lectures_start).days + 1))
    return len({d.isocalendar()[:2] for d in days if d.weekday() < 5 and d not in off})


def held(assignments):
    """{pk: LVS} each Lehrauftrag's hours held: the dates of its courses gone by and not called off that its person
    teaches, a course's own and their groups' and ensembles', each by the minutes its Lehrform counts to an LVS (per_sws);
    of one withdrawn, those until the day."""
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
            if a.withdrawn_on and timezone.localdate(b.end) > a.withdrawn_on:
                continue
            found[a.pk] += lvs
    return {pk: total.quantize(Decimal('0.1')) for pk, total in found.items()}


def payable(a):
    """Whether a Lehrauftrag's hours held go to the payroll: granted, also if withdrawn later; once the lectures are over
    or it is withdrawn."""
    return a.granted is not None and a.status != PROPOSED and (a.status == WITHDRAWN or timezone.localdate() > a.term.lectures_end)


def figures(rows, term):
    """Each Lehrauftrag of the rows with `teaching` (the SWS its person teaches now), `more` (granted for less), `too_much`
    (beyond the Hochschule's limit), `cost` (agreed SWS over the teaching weeks at its Vergütungsstufe, or the hours held
    once confirmed), `so_far` (the hours held, once they count) and `still` (withdrawn, yet still on courses)."""
    taught = hours(term, {a.person_id for a in rows})
    limit, span = Institution.get().assignment_max, weeks(term)
    done = held([a for a in rows if payable(a) and not a.settled_on])
    for a in rows:
        a.teaching = sum(h for _, h in taught.get(a.person, []))
        a.more = a.status == GRANTED and a.teaching > a.hours
        a.too_much = bool(limit) and a.status != WITHDRAWN and max(a.hours, a.teaching) > limit
        a.so_far = a.held if a.settled_on else done.get(a.pk, Decimal(0)) if payable(a) else None
        a.cost = a.rate and (a.so_far if a.settled_on else a.hours * span) * a.rate.euros
        a.still = a.status == WITHDRAWN and bool(taught.get(a.person))
    return rows


def reach(person):
    """The Lehraufträge the person sees: those they grant, else those of the Deputat they see."""
    return person.within(Assignment.objects.all(), 'grant_assignments' if 'grant_assignments' in person.roles else 'view_teaching_load')


@require('grant_assignments', 'view_teaching_load')
def assignments(request):
    """A term's Lehraufträge at a glance: how many stand where, what they cost; each with its courses, the SWS agreed and
    taught, its marks; opened in the side panel. Whoever sees the Deputat reads those in their reach."""
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term is None:
        return without_term(request, 'ein Semester mit Lehraufträgen')
    granting = 'grant_assignments' in person.roles
    every = reach(person).filter(term=term)
    institute, stand, q = (request.GET.get(name, '').strip() for name in ('institut', 'stand', 'q'))
    rows = every
    if institute.isdigit():
        rows = rows.filter(person__institute=institute)
    if stand in Assignment.Status.values:
        rows = rows.filter(status=stand)
    if q:
        rows = matching(rows, q, 'person__last_name', 'person__first_name', 'courses__title')
    rows = figures(list(rows.distinct().select_related('person__institute', 'rate', 'term').prefetch_related('courses').order_by('person__last_name', 'person__first_name')), term)
    counts = Counter(every.values_list('status', flat=True))
    return render(request, 'courses/assignments.html', {
        'term': term, 'terms': Term.objects.filter(assignments__isnull=False).distinct().order_by('-start'), 'rows': rows, 'granting': granting,
        'counts': counts, 'tally': [(v, label, counts[v]) for v, label in Assignment.Status.choices],
        'statuses': Assignment.Status.choices, 'stand': stand, 'q': q, 'institute': institute,
        'institutes': Institute.objects.filter(pk__in=every.values('person__institute')), 'over': timezone.localdate() > term.lectures_end,
        'sws': sum(a.hours for a in rows if a.status != WITHDRAWN), 'cost': sum(a.cost or 0 for a in rows if a.status != WITHDRAWN), 'unrated': any(a.status != WITHDRAWN and not a.rate for a in rows),
        'weeks': weeks(term),
    })


@require('grant_assignments', 'view_teaching_load')
def assignment(request, pk):
    """One Lehrauftrag, in the side panel: its courses with what each counts, what it costs, where it stands from proposal
    to the hours held, and what the office may do next; withdrawing at the foot, with a reason."""
    person = request.user
    a = get_object_or_404(reach(person).select_related('person__institute', 'term', 'rate', 'proposed_by', 'granted_by', 'settled_by').prefetch_related('courses'), pk=pk)
    figures([a], a.term)
    taught = dict(hours(a.term, {a.person_id}).get(a.person, []))
    on = set(a.courses.all())
    courses = [(c, taught.get(c), c in on) for c in sorted(on | set(taught), key=lambda c: c.title)]
    previous = Assignment.objects.filter(person=a.person, rate__isnull=False).exclude(pk=a.pk).order_by('-term__start').values_list('rate', flat=True).first()
    return render(request, 'courses/assignment.html', {
        'a': a, 'courses': courses, 'granting': 'grant_assignments' in person.roles and person.may('grant_assignments', a), 'weeks': weeks(a.term),
        'rates': Rate.objects.all(), 'rate': a.rate_id or previous, 'limit': Institution.get().assignment_max,
        'letter': a.person.messages.filter(subject__startswith=f'Bescheid: Lehrauftrag {a.term}').order_by('-created').first(),
        'history': bool(Assignment.seen_by(person, a.pk)),
    })


def chosen(request):
    return request.user.within(Assignment.objects.filter(pk__in=request.POST.getlist('auftrag')), 'grant_assignments').select_related('person', 'term', 'rate')


def back(request, a=None):
    """To the list, or the Lehrauftrag acted on from its panel."""
    if a and request.POST.get('von') == 'auftrag':
        return redirect('courses:assignment', a.pk)
    return redirect(f"{reverse('courses:assignments')}?semester={request.POST.get('semester', '')}")


def bescheid(a, by):
    """The Bescheid granting a Lehrauftrag, to the person's Postfach: its term and period, courses with their SWS, scope
    and pay."""
    taught = dict(hours(a.term, {a.person_id}).get(a.person, []))
    texts.send(
        'lehrauftrag', a.person, reverse('courses:mine'), semester=a.term, von=by,
        zeitraum=f'{date_format(a.term.lectures_start, "j.n.Y")} bis {date_format(a.term.lectures_end, "j.n.Y")}',
        umfang=f'{number_format(a.hours)} SWS' if a.hours else 'nach gehaltenen Stunden',
        veranstaltungen='; '.join(f'{c.title} ({number_format(taught.get(c) or 0)} SWS)' for c in sorted(a.courses.all(), key=lambda c: c.title)),
        verguetung=f'{number_format(a.rate.euros, 2)} € je gehaltener Lehrveranstaltungsstunde ({a.rate.name})' if a.rate else 'nach den Vergütungssätzen der Hochschule',
    )


@require('grant_assignments')
@require_POST
def grant(request):
    """Grant the Lehraufträge ticked, or the one from its panel, at what each teaches now, with those courses, at the
    Vergütungsstufe chosen or the person's last; the Bescheid goes to the person. Granting one again agrees the SWS and
    courses it has come to."""
    rate = Rate.objects.filter(pk=request.POST.get('stufe') or None).first()
    limit, granted, beyond, a = Institution.get().assignment_max, 0, [], None
    for a in chosen(request).exclude(status=WITHDRAWN):
        courses = list(a.term.courses.filter(kind__in=TEACHING, cancelled=None, staff=a.person))
        if not courses:
            continue
        a.status, a.granted_by, a.granted, a.accepted = GRANTED, request.user, timezone.localdate(), None  # a new Bescheid, to accept anew
        a.hours = agreed(a.term.courses.filter(pk__in=[c.pk for c in courses]), a.person)
        a.rate = rate or a.rate or Rate.objects.filter(assignments__person=a.person).order_by('-assignments__term__start').first()
        a.save()
        a.courses.set(courses)
        bescheid(a, request.user)
        granted += 1
        if limit and a.hours > limit:
            beyond.append(a.person.last_name)
    if granted:
        messages.success(request, f'{plural(granted, "Lehrauftrag", "Lehraufträge")} erteilt; {"der Bescheid ist" if granted == 1 else "die Bescheide sind"} im Postfach.')
    else:
        messages.error(request, 'Nichts erteilt: ohne Lehrveranstaltung kein Lehrauftrag.')
    if beyond:
        messages.warning(request, f'Über {number_format(limit)} SWS: {", ".join(beyond)}. Viel Lehre im Auftrag kann als Beschäftigung gelten.')
    return back(request, a)


@require('grant_assignments')
@require_POST
def withdraw(request, pk):
    """Withdraw a Lehrauftrag for a reason, or decline a proposal: a granted one's Bescheid tells the person, the hours
    held until today still paid. Ticked, the person leaves its courses, their groups included, so nothing proposes it
    again; else the page says whom they still teach."""
    a = get_object_or_404(request.user.within(Assignment.objects.select_related('person', 'term'), 'grant_assignments'), pk=pk)
    if not (reason := request.POST.get('grund', '').strip()[:200]):
        messages.error(request, 'Bitte den Grund angeben.')
        return back(request, a)
    was, today = a.status, timezone.localdate()
    a.status, a.note, a.withdrawn_on = WITHDRAWN, reason, today
    a.save()
    if request.POST.get('austragen'):
        for c in a.term.courses.filter(staff=a.person):
            c.staff.remove(a.person)
        for g in Group.objects.filter(course__term=a.term, staff=a.person):
            g.staff.remove(a.person)
    if was == GRANTED:
        texts.send('lehrauftrag_zurueckgezogen', a.person, reverse('courses:mine'), semester=a.term, grund=reason, tag=date_format(today, 'j.n.Y'), von=request.user)
    messages.success(request, f'Lehrauftrag {a.person} {"zurückgezogen" if was == GRANTED else "nicht erteilt"}.')
    return back(request, a)


@require('grant_assignments')
@require_POST
def settle(request):
    """Confirm the hours held of the Lehraufträge ticked, or the one from its panel, once they count (payable): the
    figure the payroll gets."""
    rows = [a for a in chosen(request).filter(settled_on=None).prefetch_related('courses') if payable(a)]
    done = held(rows)
    for a in rows:
        a.held, a.settled_by, a.settled_on = done.get(a.pk, Decimal(0)), request.user, timezone.localdate()
        a.save()
    messages.success(request, f'Stunden von {plural(len(rows), "Lehrauftrag", "Lehraufträgen")} bestätigt.' if rows else 'Nichts zu bestätigen: erst nach der Vorlesungszeit.')
    return back(request, rows[0] if len(rows) == 1 else None)


@require('grant_assignments')
def export(request):
    """A term's Lehraufträge granted, also those withdrawn later, as CSV for the payroll: agreed, held and what it pays."""
    term = get_object_or_404(Term, pk=request.GET.get('semester') or None)
    rows = request.user.within(term.assignments.filter(granted__isnull=False), 'grant_assignments').select_related('person__institute', 'settled_by', 'rate').prefetch_related('courses')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="lehrauftraege-{term.pk}.csv"'
    response.write('\N{BYTE ORDER MARK}')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Personalnummer', 'Nachname', 'Vorname', 'Institut', 'Lehrveranstaltungen', 'Vereinbart (SWS)', 'Erteilt am', 'Angenommen am', 'Zurückgezogen am',
        'Gehalten (LVS)', 'Vergütungsstufe', 'Je LVS (€)', 'Betrag (€)', 'Von der Person bestätigt', 'Bestätigt von', 'Bestätigt am',
    ])
    rows = list(rows.order_by('person__last_name', 'person__first_name'))
    Export.log(request.user, f'Lehraufträge {term}', len(rows))

    def day(d):
        return f'{d:%d.%m.%Y}' if d else ''

    for a in rows:
        writer.writerow([
            a.person.personnel_number, a.person.last_name, a.person.first_name, a.person.institute or '', ', '.join(sorted(c.title for c in a.courses.all())),
            number_format(a.hours), day(a.granted), day(a.accepted and timezone.localdate(a.accepted)), day(a.withdrawn_on), '' if a.held is None else number_format(a.held),
            a.rate.name if a.rate else '', number_format(a.rate.euros, 2) if a.rate else '', number_format(a.held * a.rate.euros, 2) if a.rate and a.held is not None else '',
            day(a.affirmed and timezone.localdate(a.affirmed)), a.settled_by or '', day(a.settled_on),
        ])
    return response


def own(request, pk):
    """The person's own Lehrauftrag, once granted, also if withdrawn later."""
    return get_object_or_404(Assignment.objects.select_related('term'), pk=pk, person=request.user, granted__isnull=False)


@require_POST
def accept(request, pk):
    """A Lehrbeauftragte:r accepts their Lehrauftrag, as its Bescheid asks: the office sees it in the list."""
    a = own(request, pk)
    if a.status != GRANTED:
        return redirect(f"{reverse('courses:mine')}?semester={a.term_id}")
    if not a.accepted:
        a.accepted = timezone.now()
        a.save(update_fields=['accepted'])
        messages.success(request, f'Lehrauftrag {a.term} angenommen.')
    return redirect(f"{reverse('courses:mine')}?semester={a.term_id}")


@require_POST
def affirm(request, pk):
    """A Lehrbeauftragte:r confirms the dates held of their Lehrauftrag, once they count (payable)."""
    a = own(request, pk)
    if not payable(a):
        messages.error(request, 'Bestätigen lässt sich nach dem Ende der Vorlesungszeit.')
    elif not a.affirmed:
        a.affirmed = timezone.now()
        a.save(update_fields=['affirmed'])
        messages.success(request, f'Gehaltene Stunden bestätigt; {Office.holding("grant_assignments") or "das zuständige Amt"} gibt sie zur Vergütung weiter.')
    return redirect(f"{reverse('courses:mine')}?semester={a.term_id}")
