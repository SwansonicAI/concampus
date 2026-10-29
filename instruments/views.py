from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import texts
from core.access import require
from core.models import Institute, Term
from core.text import matching
from .forms import LendForm
from .models import Instrument, Loan

STATES = [('frei', 'frei'), ('verliehen', 'verliehen'), ('ueberfaellig', 'überfällig'), ('gesperrt', 'nicht verleihbar')]


def state(instrument):
    """What the list says of an instrument; needs `open`, its loan not yet returned, from `pool`."""
    if instrument.open:
        return 'ueberfaellig' if instrument.open[0].overdue else 'verliehen'
    return 'frei' if instrument.available else 'gesperrt'


def pool(person):
    """The instruments the person looks after, each with `open` (its current loan, if any)."""
    return (
        person.within(Instrument.objects.all(), 'manage_instruments').select_related('institute')
        .prefetch_related(Prefetch('loans', Loan.objects.filter(returned=None).select_related('person'), to_attr='open'))
    )


@require('manage_instruments')
def index(request):
    instruments = pool(request.user)
    stand, institute, q = (request.GET.get(key, '').strip() for key in ('stand', 'institut', 'q'))
    if institute.isdigit():
        instruments = instruments.filter(institute=institute)
    instruments = list(matching(instruments, q, 'name', 'number', 'maker', 'loans__person__last_name') if q else instruments)
    for i in instruments:
        i.state = state(i)
    return render(request, 'instruments/index.html', {
        'instruments': [i for i in instruments if not stand or i.state == stand],
        'states': [(key, label, sum(i.state == key for i in instruments)) for key, label in STATES],
        'stand': stand, 'institute': institute, 'q': q,
        'institutes': Institute.objects.filter(instruments__in=pool(request.user).values('pk')).distinct(),
    })


@require('manage_instruments')
def instrument(request, pk):
    instrument = get_object_or_404(pool(request.user), pk=pk)
    term = Term.current()  # lent for the semester, as a rule
    return render(request, 'instruments/instrument.html', {
        'instrument': instrument, 'state': state(instrument), 'loans': instrument.loans.select_related('person'),
        'lending': LendForm(initial={'due': term and term.end, 'condition': instrument.note[:200]}) if state(instrument) == 'frei' else None,
    })


@require('manage_instruments')
@require_POST
def lend(request, pk):
    instrument = get_object_or_404(pool(request.user), pk=pk)
    form = LendForm(request.POST)
    if state(instrument) != 'frei':
        messages.error(request, 'Das Instrument ist nicht frei.')
    elif not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
    else:
        data = form.cleaned_data
        loan = Loan.objects.create(instrument=instrument, person=data['who'], due=data['due'], note=data['note'], condition=data['condition'])
        texts.send('leihe', loan.person, reverse('instruments:mine'), instrument=instrument.name, frist=f'{loan.due:%d.%m.%Y}')
        messages.success(request, f'{instrument.name} an {loan.person} verliehen, zurück bis {date_format(loan.due, "j.n.Y")}.')
    return redirect('instruments:instrument', pk=instrument.pk)


@require('manage_instruments')
@require_POST
def give_back(request, pk):
    loan = get_object_or_404(request.user.within(Loan.objects.filter(returned=None), 'manage_instruments'), pk=pk)
    loan.returned, loan.returned_condition = timezone.localdate(), request.POST.get('condition', '').strip()[:200]
    loan.save(update_fields=['returned', 'returned_condition'])
    messages.success(request, f'{loan.instrument.name} zurückgenommen.')
    return redirect('instruments:instrument', pk=loan.instrument_id)


@require('manage_instruments')
@require_POST
def remind(request, pk):
    """Remind whoever has an instrument overdue; once a day."""
    loan = get_object_or_404(request.user.within(Loan.objects.filter(returned=None, due__lt=timezone.localdate()), 'manage_instruments').select_related('instrument', 'person'), pk=pk)
    if loan.reminded == timezone.localdate():
        messages.success(request, 'Heute schon erinnert.')
    else:
        texts.send('leihe_erinnerung', loan.person, reverse('instruments:mine'), instrument=loan.instrument.name, frist=f'{loan.due:%d.%m.%Y}')
        loan.reminded = timezone.localdate()
        loan.save(update_fields=['reminded'])
        messages.success(request, f'{loan.person} erinnert.')
    return redirect('instruments:instrument', pk=loan.instrument_id)


@require('manage_instruments')
def agreement(request, pk):
    """The loan agreement to print and sign at handover."""
    loan = get_object_or_404(request.user.within(Loan.objects.all(), 'manage_instruments').select_related('instrument', 'person'), pk=pk)
    return render(request, 'instruments/agreement.html', {'loan': loan, 'terms': texts.block('leihbedingungen'), 'sender': texts.block('absender_leihe')})


def mine(request):
    """The instruments the person has borrowed, out now and before."""
    return render(request, 'instruments/mine.html', {'loans': request.user.loans.select_related('instrument')})
