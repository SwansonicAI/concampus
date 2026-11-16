"""A semester's Eignungsprüfungen as the office sets them up and runs them: each Hauptfach's, its stages in order and
what each needs next; taken over from an earlier semester."""
from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.access import require
from core.apps import plural
from core.models import Term
from core.text import collated, fold, words
from courses.models import Course, Participation
from courses.views.rollover import copy_auditions
from rooms.models import Poll
from students.models import Document, Study
from ..auditions import booked, chains, days, need

URGENT = {'no': 3, 'open': 2, 'wait': 1}  # how urgent a step is, by what its badge says


def staged(person, term):
    """The stages of a semester the person runs, as chains() groups them, each with `size` (applicants), `step`
    (next_step) and `days` (when held in person)."""
    if term is None:
        return [], []
    today = timezone.localdate()
    courses = Course.panels(
        person.within(Course.objects.filter(term=term, kind=Course.Kind.AUDITION), 'run_auditions').select_related('program', 'subject')
        .annotate(date=Min('bookings__start'), until=Max('bookings__start'), vacant=Count('participations__recusals', filter=Q(participations__recusals__substitute=None), distinct=True))
    )
    grouped, shared = chains(courses)
    rows = defaultdict(list)
    for r in Participation.objects.filter(course__in=courses).values('course', 'study', 'passed', 'remark', 'slot', 'study__status'):
        rows[r['course']].append(r)
    entered = {(c, r['study']) for c, found in rows.items() for r in found}
    handed = set(Document.objects.filter(study__in={s for _, s in entered}, kind__jury=True, kind__shared=False).values_list('study', flat=True))
    polls = {p.course_id: p for p in Poll.objects.filter(course__in=courses, chosen=None).annotate(answered=Count('options__answers__person', distinct=True), size=Count('people', distinct=True))}
    for c in courses:
        c.poll = polls.get(c.pk)
        c.lacking = (plan.hours - booked(c)) if not (c.video or c.released) and c.date and (plan := need(c)) else 0  # booked days too short
        c.size, c.step, c.days = len(rows[c.pk]), next_step(c, rows[c.pk], handed, entered, today), days(c.date, c.until)
    return grouped, shared


def next_step(c, rows, handed, entered, today):
    """What a stage needs next, as (badge, words), or None while nothing is due: a commission to complete, its day to
    find (`poll`, a Terminumfrage running), dates to give, results to enter, the stage to close, those who passed to
    invite."""
    applied = Study.Status.APPLIED
    if c.released:
        n = sum(1 for r in rows if r['passed'] and r['study__status'] == applied and not any((f, r['study']) in entered for f in c.next_pks))
        return ('open', f'{n} einladen') if n else ('green', 'abgeschlossen')
    if c.shortfall:
        return 'no', 'Kommission zu klein'
    if c.vacant:
        return 'no', 'Ersatz bestellen'
    if not c.video and not c.date:  # the day comes first, before anyone applies
        if c.poll and (c.poll.answered >= c.poll.size or c.poll.by < today):
            return 'open', 'Termin wählen'
        return ('wait', 'Antworten abwarten') if c.poll else ('open', 'Tag festlegen')
    if c.lacking > 0:
        return ('wait', 'Antworten abwarten') if c.poll else ('open', 'Tag hinzufügen')
    if not rows or c.deadline and c.deadline >= today:  # nobody yet, or applications still coming in
        return None
    if not c.video:
        if n := sum(1 for r in rows if r['slot'] is None and r['passed'] is None and r['study__status'] == applied):
            return 'open', f'{n} ohne Termin'
        if c.date > timezone.now():
            return None
    due = [r for r in rows if r['passed'] is None and r['remark'] != Participation.Remark.EXCUSED and (not c.video or r['study'] in handed)]
    if due:
        return ('no' if c.marks_by and c.marks_by < today else 'wait'), f'{len(due)} ohne Ergebnis'
    return 'open', 'abschließen'


def lane(own, shared=False):
    """A Hauptfach's Eignungsprüfung as a row: the stage under way (`now`, the `at`th of `count`), the days held in
    person, and the most urgent step with the stage it is at (`due`); a stage several Hauptfächer share, alone."""
    first, now = own[0], next((s for s in own if not s.released), None)
    due = max((s for s in own if s.step and s.step[0] in URGENT), key=lambda s: URGENT[s.step[0]], default=None)
    held = [s for s in own if s.date]
    name = first.stage if shared else str(first.subject or first.title)
    return {
        'name': name, 'first': first, 'program': first.program, 'shared': shared, 'size': first.size, 'capacity': first.capacity,
        'now': now, 'at': own.index(now) + 1 if now else 0, 'count': len(own),
        'days': days(min(s.date for s in held), max(s.until for s in held)) if held else '',
        'poll': next((s.poll for s in own if s.poll), None),  # a day still being found
        'step': due and due.step, 'due': due, 'urgency': URGENT[due.step[0]] if due else 0, 'done': now is None and not due,
        'text': fold(' '.join([name, str(first.program or ''), *(s.title for s in own)])),
    }


@require('run_auditions')
def index(request):
    """The Eignungsprüfungen of a semester, a row each, to search, filter by what is to do, and sort."""
    today = timezone.localdate()
    terms = Term.objects.filter(Q(courses__kind=Course.Kind.AUDITION) | Q(start__gt=today)).distinct().order_by('-start')
    picked = request.GET.get('semester', '')
    term = (picked.isdigit() and terms.filter(pk=picked).first()) or terms.filter(start__gt=today).last() or terms.first()
    grouped, shared = staged(request.user, term)
    lanes = [lane(own) for own, _ in grouped] + [lane([c], shared=True) for c in shared]
    q, state = request.GET.get('q', '').strip(), request.GET.get('stand', '')
    sought, keep = words(q), {'offen': lambda l: l['urgency'], 'fertig': lambda l: l['done']}.get(state, bool)
    lanes = [l for l in lanes if keep(l) and all(w in l['text'] for w in sought)]
    return render(request, 'admissions/auditions.html', {
        'term': term, 'terms': terms, 'q': q, 'state': state, 'coming': term and term.start > today,
        'lanes': sorted(lanes, key=lambda l: (l['shared'], collated(l['name']), l['program'] and l['program'].degree or '')),
    })


@require('run_auditions')
def intake(request, pk):
    """One Hauptfach's Eignungsprüfung: what it admits to, and its stages in order, its own then those it shares, each
    with when, who judges and what it needs next."""
    own, shared, _ = audition_of(request, pk)
    return render(request, 'admissions/intake.html', {
        'first': own[0], 'own': own, 'shared': shared, 'trail': [(None, f"{reverse('admissions:auditions')}?semester={own[0].term_id}")],
    })


def audition_of(request, pk):
    """The stage applied for that a Hauptfach's Eignungsprüfung starts with, as the office running it sees it: with its own
    stages after it and those it shares, and the semester's shared stages it does not take yet (`others`)."""
    first = get_object_or_404(request.user.within(Course.objects.filter(kind=Course.Kind.AUDITION, program__isnull=False), 'run_auditions'), pk=pk)
    grouped, every = staged(request.user, first.term)
    own, shared = next(((o, s) for o, s in grouped if o[0].pk == first.pk), ([first], []))
    return own, shared, [c for c in every if c not in shared]


@require('run_auditions')
def add_step(request, pk):
    """A stage for a Hauptfach's Eignungsprüfung: one the semester's Hauptfächer sit together, as Klavier, joined with a
    click, its date and commission as they are; or a new one of its own."""
    own, _, others = audition_of(request, pk)
    if request.method == 'POST':
        stage = next((c for c in others if str(c.pk) == request.POST.get('stufe')), None)
        if stage:
            stage.after.add(own[-1])  # sat by whoever passes this Hauptfach's last stage of its own
            messages.success(request, f'{stage.stage} gehört jetzt auch zu {own[0].subject or own[0].title}.')
        return redirect('admissions:intake', pk)
    return render(request, 'admissions/add_step.html', {'first': own[0], 'last': own[-1], 'others': others})


@require('run_auditions')
@require_POST
def drop_step(request, pk, stage):
    """A stage the Hauptfächer share, no longer sat by this one's applicants; the stage stays for the others."""
    own, shared, _ = audition_of(request, pk)
    if found := next((c for c in shared if c.pk == stage), None):
        found.after.remove(*own)
        messages.success(request, f'{found.stage} gehört nicht mehr zu {own[0].subject or own[0].title}.')
    return redirect('admissions:intake', pk)


@require('run_auditions')
def carry(request):
    """Eignungsprüfungen of an earlier semester into one to come, by default from the same semester a year before: each
    Hauptfach's with its stages and the parts it shares, those the semester has already left out. ?nur= ticks one."""
    data, near = request.POST or request.GET, timedelta(days=35)  # semesters start on about the same day each year
    held = Term.objects.filter(courses__kind=Course.Kind.AUDITION).distinct().order_by('-start')
    source = data.get('aus', '').isdigit() and held.filter(pk=data['aus']).first()
    targets = Term.objects.filter(start__gt=max(timezone.localdate(), source.start) if source else timezone.localdate()).order_by('start')
    target = data.get('nach', '').isdigit() and targets.filter(pk=data['nach']).first()
    if not target:
        target = (source and targets.filter(start__range=(source.start + timedelta(days=365) - near, source.start + timedelta(days=365) + near)).first()) or targets.first()
    sources = held.filter(start__lt=target.start) if target else held.none()
    if not source and target:
        source = sources.filter(start__range=(target.start - timedelta(days=365) - near, target.start - timedelta(days=365) + near)).first() or sources.first()
    rows = []
    if source and target:
        courses = list(request.user.within(Course.objects.filter(term=source, kind=Course.Kind.AUDITION), 'run_auditions').select_related('program', 'subject'))
        there = set(Course.objects.filter(term=target, kind=Course.Kind.AUDITION).values_list('title', flat=True))
        rows = sorted(((own, shared, own[0].title in there) for own, shared in chains(courses)[0]), key=lambda r: (collated(str(r[0][0].subject or r[0][0].title)), r[0][0].title))
    if request.method == 'POST' and rows:
        picked = set(request.POST.getlist('stufe'))
        chosen = {s.pk: s for own, shared, done in rows if str(own[0].pk) in picked and not done for s in [*own, *(c for c in shared if c.title not in there)]}
        with transaction.atomic():
            n = copy_auditions(list(chosen.values()), target)
        messages.success(request, f'{plural(n, "Stufe", "Stufen")} aus dem {source} ins {target} übernommen. Termine und Räume sind neu zu planen.')
        return redirect(f"{reverse('admissions:auditions')}?semester={target.pk}")
    return render(request, 'admissions/carry.html', {
        'sources': sources, 'source': source, 'targets': targets, 'target': target, 'rows': rows, 'only': data.get('nur', ''),
        'weeks': source and target and round((target.start - source.start).days / 7),
        'trail': [(None, reverse('admissions:auditions'))],
    })
