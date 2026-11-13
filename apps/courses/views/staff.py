"""Lehrende as the office taking teachers on keeps them: who teaches here, on what terms, from when to when, whether they
have signed in; each a record in the side panel with their Lehrverpflichtung and Deputat over the semesters. Whoever
sees the Deputat reads those in their reach."""
from datetime import timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core.access import require
from core.models import Institute, Person, Term
from core.text import matching
from core.transfer import COLUMNS
from setup.views import uninvited
from ..models import Assignment, Obligation, Reduction
from .common import TEACHING
from .load import hours, owing


def reach(person):
    """The teachers the person keeps, else those whose Deputat they see."""
    return person.within(Person.teachers(), 'manage_staff' if 'manage_staff' in person.roles else 'view_teaching_load')


@require('manage_staff', 'view_teaching_load')
def staff(request):
    """The teachers, by Institut, Beschäftigung and what needs doing: without a Lehrverpflichtung while teaching this
    term, a contract ending, never signed in, left. Each with their Soll this term, or their Lehrauftrag."""
    person, term, today = request.user, Term.current(), timezone.localdate()
    every = reach(person)
    teaching = Q(teaching__term=term, teaching__kind__in=TEACHING, teaching__cancelled=None) if term else Q(pk=None)
    stands = {
        'ohne': ('ohne Lehrverpflichtung', every.filter(teaching, is_active=True).exclude(employment=Person.Employment.LEHRAUFTRAG).exclude(obligations__term__start__lte=term.start if term else today)),
        'befristet': ('befristet, bald zu Ende', every.filter(is_active=True, contract_end__lte=today + timedelta(weeks=8))),
        'neu': ('noch nie angemeldet', every.filter(is_active=True, last_login=None)),
        'aus': ('ausgeschieden', every.filter(is_active=False)),
    }
    institute, kind, stand, q = (request.GET.get(name, '').strip() for name in ('institut', 'art', 'stand', 'q'))
    rows = stands[stand][1] if stand in stands else every.filter(is_active=True)
    if institute.isdigit():
        rows = rows.filter(institute=institute)
    if kind in Person.Employment.values:
        rows = rows.filter(employment=kind)
    if q:
        rows = matching(rows, q, 'last_name', 'first_name', 'personnel_number', 'email')
    rows = list(rows.distinct().select_related('institute').order_by('last_name', 'first_name'))
    soll = owing(rows, [term]) if term else {}
    contracts = {a.person_id: a for a in Assignment.objects.filter(term=term, person__in=rows)} if term else {}
    for p in rows:
        p.soll, p.contract = soll.get((p.pk, term and term.pk)), contracts.get(p.pk)
        p.ending = p.contract_end and p.contract_end <= today + timedelta(weeks=8)
    return render(request, 'courses/staff.html', {
        'rows': rows, 'term': term, 'keeping': 'manage_staff' in person.roles, 'uninvited': uninvited(person).count() if 'manage_staff' in person.roles else 0,
        'stands': [(key, label, found.distinct().count()) for key, (label, found) in stands.items()], 'stand': stand,
        'institutes': Institute.objects.filter(pk__in=every.values('institute')), 'institute': institute, 'kinds': Person.Employment.choices, 'kind': kind, 'q': q,
        'columns': ', '.join(COLUMNS),
    })


@require('manage_staff', 'view_teaching_load')
def teacher(request, pk):
    """A teacher's record, in the side panel: who they are and on what terms; per semester, the last two, this one and the
    one being planned, their Soll with where it comes from, Ermäßigungen, Deputat and Lehrauftrag; the Lehrverpflichtungen
    entered, each from its semester on; this term's courses."""
    person = request.user
    t = get_object_or_404(reach(person).select_related('institute'), pk=pk)
    current, upcoming = Term.current(), Term.upcoming()
    last = upcoming or current
    terms = list(reversed(Term.objects.filter(start__lte=last.start).order_by('-start')[:4])) if last else []
    soll, force = owing([t], terms), Obligation.in_force([t], terms)
    reduced = {}
    for r in Reduction.objects.filter(person=t, term__in=terms).select_related('ground'):
        reduced.setdefault(r.term_id, []).append(r)
    contracts = {a.term_id: a for a in t.assignments.filter(term__in=terms)}
    semesters = []
    for term in terms:
        taught = sum(h for _, h in hours(term, {t.pk}).get(t, []))
        due = soll.get((t.pk, term.pk))
        due = None if due is None else due - sum(r.hours for r in reduced.get(term.pk, []))
        semesters.append({
            'term': term, 'soll': soll.get((t.pk, term.pk)), 'obligation': force.get((t.pk, term.pk)), 'reductions': reduced.get(term.pk, []),
            'taught': taught, 'balance': None if due is None else taught - due, 'contract': contracts.get(term.pk), 'now': term == current,
            'past': bool(current) and term.start < current.start,  # a gap there is history, not a task
        })
    return render(request, 'courses/teacher.html', {
        't': t, 'semesters': semesters, 'keeping': 'manage_staff' in person.roles and person.may('manage_staff', t),
        'obligations': t.obligations.select_related('term', 'post').order_by('-term__start'),
        'courses': t.teaching.filter(term=current, kind__in=TEACHING).annotate(n=Count('participations', distinct=True)).order_by('title') if current else [],
        'ending': t.contract_end and t.contract_end <= timezone.localdate() + timedelta(weeks=8),
    })
