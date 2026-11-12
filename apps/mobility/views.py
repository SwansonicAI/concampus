from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, OuterRef
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format

from core import dates
from core.access import require
from core.apps import plural
from core.models import Term
from core.text import matching
from courses.models import Participation
from .forms import LineForm, MobilityForm, academic
from .models import Component, Mobility, Partner, year


@require('manage_mobility')
def index(request):
    """Auslandsaufenthalte of a Studienjahr, with the form to note a new one."""
    today = timezone.localdate()
    years = sorted({academic(d) for d in Mobility.objects.values_list('start', flat=True)} | {academic(today)}, reverse=True)
    chosen = int(request.GET['jahr']) if request.GET.get('jahr', '').isdigit() else academic(today)
    stand, q = request.GET.get('stand', ''), request.GET.get('q', '').strip()
    form = MobilityForm(request.POST or None)
    if form.is_valid():
        mobility = form.save()
        messages.success(request, f'Auslandsaufenthalt {mobility.study.person} an der {mobility.partner} erfasst; nun füllt die oder der Studierende das Learning Agreement aus.')
        return redirect(mobility)
    shown = Mobility.objects.filter(start__gte=f'{chosen}-09-01', start__lt=f'{chosen + 1}-09-01').select_related('study__person', 'study__program', 'study__subject', 'partner')
    shown = matching(shown, q, 'study__person__first_name', 'study__person__last_name', 'partner__name', 'partner__city') if q else shown
    shown = list(shown.annotate(lines=Count('components')))
    states = [(value, label, sum(m.status == value for m in shown)) for value, label in Mobility.Status.choices]
    return render(request, 'mobility/index.html', {
        'mobilities': [m for m in shown if not stand or m.status == stand], 'states': states, 'stand': stand, 'q': q, 'form': form,
        'years': [(y, year(y)) for y in years], 'chosen': chosen,
    })


@require('manage_mobility')
def partners(request):
    """Partnerhochschulen with their agreements, and how many places of this Studienjahr are taken."""
    now = academic(timezone.localdate())
    taken = dict(
        Mobility.objects.exclude(status=Mobility.Status.CANCELLED).filter(start__gte=f'{now}-09-01', start__lt=f'{now + 1}-09-01')
        .values_list('agreement').annotate(n=Count('pk')),
    )
    q = request.GET.get('q', '').strip()
    found = Partner.objects.prefetch_related('agreements__subjects').order_by('name')
    found = list(matching(found, q, 'name', 'city', 'erasmus') if q else found)
    for a in (a for p in found for a in p.agreements.all()):
        a.taken = taken.get(a.pk, 0)
    return render(request, 'mobility/partners.html', {'partners': found, 'now': now, 'q': q, 'current': year(now)})


def mobility(request, pk):
    """An Auslandsaufenthalt with its Learning Agreement: the student fills it in and signs, the office here approves and
    notes the partner's approval until Erasmus Without Paper brings it; the lines that count here go to the Anerkennung
    once the student is back."""
    found = get_object_or_404(Mobility.objects.select_related('study__person', 'study__program', 'partner', 'agreement', 'signed_here_by'), pk=pk)
    person, office = request.user, request.user.everywhere('manage_mobility')
    own = found.study.person_id == person.pk
    if not (own or office or person.may('recognise', found.study)):  # whoever recognises reads it, to recognise what it lists
        raise PermissionDenied
    editing = found.open and (own or office)
    if request.method == 'POST':
        act(request, found, own, office, editing)
        return redirect(found)
    lines = list(found.components.select_related('module'))
    passed = set(Participation.objects.filter(study=found.study, passed=True, module__in=[c.module_id for c in lines if c.module_id]).values_list('module', flat=True))
    home = [(c, c.module_id in passed) for c in lines if c.table == Component.Table.HOME]
    return render(request, 'mobility/mobility.html', {
        'mobility': found, 'abroad': [c for c in lines if c.table == Component.Table.ABROAD], 'home': home, 'own': own, 'office': office, 'editing': editing,
        'credits': (sum(c.credits for c in lines if c.table == 'A'), sum(c.credits for c in lines if c.table == 'B')),
        'adding': {t: LineForm(initial={'table': t}, study=found.study) for t in Component.Table.values} if editing else {},
        'recognising': found.status == Mobility.Status.DONE and person.may('recognise', found.study),
        'taken': getattr(Term.objects.filter(start__lte=found.start).order_by('-start').first(), 'name', ''),  # the semester it counts as taken in
        'states': Mobility.Status.choices,
    })


def act(request, found, own, office, editing):
    """What a POST on an Auslandsaufenthalt does: a line added or taken out, a signature, a change of its state."""
    post, now = request.POST, timezone.now()
    if 'zeile' in post and editing:
        form = LineForm(post, study=found.study)
        if form.is_valid():
            form.instance.mobility = found
            form.save()
            found.changed()
            messages.success(request, 'Zeile ergänzt.' + (' Die Unterschriften sind wieder offen.' if found.changes else ''))
        else:
            messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
    elif (pk := post.get('entfernen')) and editing:
        found.components.filter(pk=pk).delete()
        found.changed()
        messages.success(request, 'Zeile entfernt.')
    elif 'unterschreiben' in post and own and editing and found.components.filter(table='A').exists() and found.components.filter(table='B').exists():
        found.signed_student = now
        found.save(update_fields=['signed_student'])
        messages.success(request, 'Learning Agreement unterschrieben; die Hochschule prüft es.')
    elif 'genehmigen' in post and office and found.signed_student and not found.signed_here:
        found.signed_here, found.signed_here_by = now, request.user
        found.save(update_fields=['signed_here', 'signed_here_by'])
        messages.success(request, 'Für die Hochschule genehmigt; nun die Partnerhochschule.')
    elif 'partner' in post and office and found.signed_here:
        if day := dates.day(post.get('am', '')):
            found.signed_partner = day
            found.save(update_fields=['signed_partner'])
            messages.success(request, f'Genehmigung der {found.partner} vom {date_format(day, "j.n.Y")} vermerkt.')
        else:
            messages.error(request, 'Bitte den Tag angeben, an dem die Partnerhochschule genehmigt hat.')
    elif (status := post.get('stand')) in Mobility.Status.values and office:
        found.status = status
        found.save(update_fields=['status'])
        messages.success(request, f'Stand: {found.get_status_display()}.')


def mine(request):
    """One's own Auslandsaufenthalt, the latest."""
    found = Mobility.objects.filter(study__person=request.user).first()
    if found is None:
        raise PermissionDenied
    return redirect(found)


def waiting(person):
    """Learning Agreements the student has signed and the Hochschule not yet."""
    return Mobility.objects.filter(signed_student__isnull=False, signed_here=None).exclude(status=Mobility.Status.CANCELLED) if person.everywhere('manage_mobility') else Mobility.objects.none()


def unrecognised(person):
    """Stays over whose lines for here are not all recognised yet, for whoever recognises in their study."""
    if 'recognise' not in person.roles:
        return []
    passed = Participation.objects.filter(study=OuterRef('mobility__study'), module=OuterRef('module'), passed=True)
    lines = Component.objects.filter(table=Component.Table.HOME).filter(~Exists(passed))
    found = Mobility.objects.filter(status=Mobility.Status.DONE, pk__in=lines.values('mobility')).select_related('study__person', 'partner')
    return [m for m in found if person.may('recognise', m.study)]
