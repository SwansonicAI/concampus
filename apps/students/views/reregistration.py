"""The Rückmeldung: Semesterbeiträge paid and taken back, and the health insurers' reports."""
from decimal import Decimal
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import dates, texts
from core.access import require
from core.apps import plural
from core.models import Institution, Person, Term
from core.shortcuts import SHOWN, preview, without_term
from core.text import fold, matching
from exams.results import exhausted
from instruments.models import Loan
from .. import payments, smv
from ..models import InsuranceReport, Study, StudyTerm


class PaymentForm(forms.Form):
    """A payment booked by hand: when it arrived, how much, and the Beleg to find it again."""

    paid = dates.ReceivedField(label='Eingegangen am')
    amount = forms.DecimalField(label='Betrag', min_value=0, decimal_places=2, localize=True)
    reference = forms.CharField(label='Beleg oder Verwendungszweck', max_length=140)


@require('manage_students')
@require_POST
def fee_paid(request, pk):
    """The Semesterbeitrag of an admitted applicant has arrived: booked by hand with day, amount and Beleg, as they have no
    Matrikelnummer yet to find it by in the bank statement."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('start_term'), 'manage_students'), pk=pk, status=Study.Status.ADMITTED)
    form, fee = PaymentForm(request.POST), study.start_term.fee
    if not form.is_valid():
        messages.error(request, ' '.join(f'{form.fields[name].label}: {" ".join(errors)}' for name, errors in form.errors.items()))
    elif form.cleaned_data['amount'] < fee:
        messages.error(request, f'Zu wenig: der Semesterbeitrag beträgt {number_format(fee, 2)} €.')
    else:
        StudyTerm.objects.filter(study=study, term=study.start_term, paid=None).update(**form.cleaned_data)
        messages.success(request, f'Semesterbeitrag verbucht: {number_format(form.cleaned_data["amount"], 2)} € am {date_format(form.cleaned_data["paid"], "j.n.Y")}.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else study.get_absolute_url())


@require('manage_students')
def insurers(request):
    """Studierendenmeldeverfahren (students/smv.py): the insurers' reports ConCampus found nobody for, to book by hand or drop;
    then every report in and out, newest first. An office for the whole Hochschule only: unbooked reports may be about
    anyone."""
    if not request.user.everywhere('manage_students'):
        raise PermissionDenied
    reports, State = InsuranceReport.objects.select_related('person', 'term'), InsuranceReport.State
    unbooked = reports.filter(state=State.OPEN, reason__in=InsuranceReport.INCOMING)
    if request.method == 'POST':
        report = get_object_or_404(reports, pk=request.POST.get('meldung'))
        open_ = report.state == State.OPEN
        if report.incoming and open_ and (person := next((p for p in smv.candidates(report) if str(p.pk) == request.POST.get('person')), None)):
            smv.book(report, person, request.user)
            messages.success(request, f'{report.get_reason_display()} verbucht für {person}.')
        elif report.incoming and open_ and 'verwerfen' in request.POST:
            report.state, report.by = State.DROPPED, request.user
            report.save(update_fields=['state', 'by'])
            messages.success(request, f'{report.get_reason_display()} der {report.insurer} verworfen.')
        elif not report.incoming and 'stornieren' in request.POST and report.state in (State.OPEN, State.DONE) and not report.cancels_id and not report.cancellations.exists():
            done = smv.cancel(report, request.user)
            messages.success(request, 'Meldung zurückgezogen, bevor sie verschickt wurde.' if done is report else 'Storno erstellt; es geht mit dem nächsten Versand.')
        elif not report.incoming and 'erneut' in request.POST and report.state == State.FAILED:
            smv.again(report, request.user)
            messages.success(request, f'{report.get_reason_display()} neu erstellt, mit der Krankenkasse aus den Stammdaten.')
        return redirect(request.get_full_path())
    q, reason, state = request.GET.get('q', '').strip(), request.GET.get('art', ''), request.GET.get('stand', '')
    shown = reports.exclude(pk__in=unbooked).prefetch_related('cancellations')
    if reason in InsuranceReport.Reason.values:
        shown = shown.filter(reason=reason)
    if state in State.values:
        shown = shown.filter(state=state)
    if (who := request.GET.get('person', '')).isdigit():
        shown = shown.filter(person=who)
    shown = matching(shown, q, 'person__first_name', 'person__last_name', 'person__matriculation', 'last_name', 'insurer', starts=True)
    rows = list(shown[:SHOWN])
    files = {}
    for s in Study.objects.filter(person__in={r.person_id for r in rows}).order_by('start_term__start'):
        files[s.person_id] = s  # each person's latest study, for the link to their file
    return render(request, 'students/insurers.html', {
        'unbooked': [(r, smv.candidates(r)) for r in unbooked], 'reports': [(r, files.get(r.person_id)) for r in rows], 'count': shown.count(),
        'q': q, 'reason': reason, 'state': state, 'reasons': InsuranceReport.Reason.choices, 'states': State.choices, 'today': timezone.localdate(),
        'waiting': reports.filter(state=State.OPEN).exclude(reason__in=InsuranceReport.INCOMING).count(),
    })


@require('manage_reregistration')
@require_POST
def unpay(request, pk):
    """Take back a Semesterbeitrag booked by mistake, for a semester not over yet; the person hears that it stands open again."""
    registration = get_object_or_404(StudyTerm.objects.select_related('study__person', 'term'), pk=pk, paid__isnull=False, term__end__gte=timezone.localdate())
    paid_on = registration.paid
    registration.paid, registration.amount, registration.reference = None, None, ''
    registration.save(update_fields=['paid', 'amount', 'reference'])
    texts.send('zahlung_zurueckgenommen', registration.study.person, registration.study.get_absolute_url(), rueckmeldesemester=registration.term, datum=date_format(paid_on, 'j.n.Y'))
    messages.success(request, f'Zahlung vom {date_format(paid_on, "j.n.Y")} für das {registration.term} zurückgenommen; {registration.study.person} ist benachrichtigt.')
    return redirect(registration.study.get_absolute_url())


def book(term, enrolled, paid):
    """Book the Semesterbeitrag {person: (day, amount, Beleg)} for all their studies and tell each where their Rückmeldung
    stands."""
    with transaction.atomic():
        for s in enrolled.filter(person__in=paid):
            day, amount, reference = paid[s.person]
            StudyTerm.objects.update_or_create(study=s, term=term, defaults={'paid': day, 'amount': amount, 'reference': reference[:140]})
    for p in paid:
        if missing := p.missing():
            texts.send('rueckmeldung_offen', p, rueckmeldesemester=term, fehlt=', '.join(missing))
        else:
            texts.send('rueckmeldung', p, rueckmeldesemester=term)


@require('manage_reregistration')
def reregistration(request):
    """Rückmeldung for the upcoming term: book payments by hand or from the bank statement; after the Nachfrist,
    exmatriculate whoever has not paid, at the end of the semester under way."""
    term, today = Term.upcoming(), timezone.localdate()
    if term is None:
        return without_term(request, 'das nächste Semester, für das sich Studierende zurückmelden')
    enrolled = (
        Study.objects.filter(status=Study.Status.ENROLLED).select_related('person', 'program', 'subject').order_by('person__last_name')
        .prefetch_related(Prefetch('person__loans', Loan.objects.filter(returned=None).select_related('instrument')))
    )
    registrations = {r.study_id: r for r in StudyTerm.objects.filter(term=term)}
    paid = {r.study.person_id for r in StudyTerm.objects.filter(term=term, paid__isnull=False).select_related('study')}
    late_fee = Institution.get().late_fee
    if request.method == 'POST':
        if 'pay' in request.POST:
            try:
                day, reference = dates.received(request.POST.get('paid', '')), request.POST.get('reference', '').strip()
            except forms.ValidationError as error:
                messages.error(request, f'Eingegangen am: {error.messages[0]}')
                return redirect('students:reregistration')
            if not reference:
                messages.error(request, 'Bitte den Beleg oder Verwendungszweck angeben, mit dem die Zahlung einging.')
                return redirect('students:reregistration')
            people = {s.person: (day, term.due(day, late_fee), reference) for s in enrolled.filter(person__in=request.POST.getlist('person'))}
            book(term, enrolled, people)
            messages.success(request, f'Zahlung für {plural(len(people), "Person", "Personen")} verbucht, je {number_format(term.due(day, late_fee), 2)} € am {date_format(day, "j.n.Y")}.')
        elif statement := request.FILES.get('statement'):  # read first, booked once checked
            unpaid = {s.person.matriculation: s.person for s in enrolled.exclude(person__in=paid)}
            request.session['kontoauszug'] = [
                {'day': day.isoformat(), 'amount': str(amount), 'text': text[:300], 'person': person and person.pk, 'short': bool(person and amount < term.due(day, late_fee))}
                for day, amount, text in payments.credits(statement.read())
                for person in [next((unpaid[n] for n in payments.numbers(text) if n in unpaid), None)]
            ]
            return redirect(f"{reverse('students:reregistration')}?auszug=1")
        elif 'buchen' in request.POST:
            lines, picked = request.session.pop('kontoauszug', []), set(request.POST.getlist('zeile'))
            people = Person.objects.in_bulk([line['person'] for line in lines if line['person']])
            found = {
                people[line['person']]: (dates.day(line['day']), Decimal(line['amount']), line['text'])
                for i, line in enumerate(lines) if str(i) in picked and line['person'] in people and not line['short'] and line['person'] not in paid
            }
            book(term, enrolled, found)
            messages.success(request, f'{plural(len(found), "Zahlung", "Zahlungen")} verbucht.')
        elif 'exmatriculate' in request.POST and today > term.closes:  # a finished study or a lost Prüfungsanspruch leaves on its own ground
            unpaid = list(enrolled.exclude(person__in=paid))
            finished = [s for s in unpaid if s.issued]
            lost = [s for s in unpaid if not s.issued and exhausted(s)]
            rest = [s for s in unpaid if s not in finished and s not in lost]
            ends = term.start - timedelta(days=1)  # with the semester under way
            values = {'rueckmeldesemester': term, 'zum': date_format(ends, 'j. F Y')}
            if not request.POST.get('bestaetigt'):
                notes = [
                    *([f'Nicht dabei, mit ausgestelltem Zeugnis: {", ".join(str(s.person) for s in finished)}. Sie werden einzeln mit dem Grund „Abschluss des Studiums“ exmatrikuliert.'] if finished else []),
                    *([f'Nicht dabei, mit erloschenem Prüfungsanspruch: {", ".join(str(s.person) for s in lost)}. Sie werden einzeln mit dem Grund „Prüfung endgültig nicht bestanden“ exmatrikuliert.'] if lost else []),
                ]
                if not rest:
                    messages.info(request, ' '.join(['Niemand, der wegen fehlender Rückmeldung zu exmatrikulieren ist.', *notes]))
                    return redirect('students:reregistration')
                return preview(
                    request, f'Exmatrikulation ohne Rückmeldung, letzter Tag {date_format(ends, "j.n.Y")}',
                    [('Keine Rückmeldung', [s.person for s in rest], rest[0].wording('exmatrikulation_rueckmeldung', **values))],
                    f'{plural(len(rest), "Studium", "Studien")} beenden und Bescheide versenden', reverse('students:reregistration'), notes,
                )
            for s in rest:
                s.set_status(Study.Status.LEFT, '7', ends)
                s.notify('exmatrikulation_rueckmeldung', **values)
            messages.success(request, f'{plural(len(rest), "Studium", "Studien")} wegen fehlender Rückmeldung zum {date_format(ends, "j.n.Y")} beendet. Bescheide versandt.')
        return redirect('students:reregistration')
    statement = request.session.get('kontoauszug', []) if request.GET.get('auszug') else []
    people = Person.objects.in_bulk([line['person'] for line in statement if line['person']])
    statement = [line | {'who': people.get(line['person']), 'day': dates.day(line['day']), 'amount': Decimal(line['amount'])} for line in statement]
    rows = [(s, registrations.get(s.pk), s.person_id in paid, s.person.missing()) for s in enrolled]
    states = {'offen': ('offen', lambda row: not row[2]), 'bezahlt': ('bezahlt', lambda row: row[2]), 'gesperrt': ('gesperrt', lambda row: row[3])}  # a Sperre or a missing Nachweis holds the Rückmeldung back
    stand, q = request.GET.get('stand', ''), request.GET.get('q', '').strip()
    words = fold(q).split()
    shown = [
        row for row in rows if (stand not in states or states[stand][1](row))
        and all(w in fold(f'{row[0].person.name} {row[0].person.matriculation}') for w in words)
    ]
    counts = {key: len({row[0].person_id for row in rows if test(row)}) for key, (_, test) in states.items()}  # people: the fee is theirs
    return render(request, 'students/reregistration.html', {
        'term': term, 'rows': shown, 'total': len({row[0].person_id for row in rows}), 'open': counts['offen'], 'blocked': counts['gesperrt'], 'overdue': today > term.closes,
        'late_fee': late_fee, 'statement': statement,
        'q': q, 'stand': stand, 'states': [(key, label, counts[key]) for key, (label, _) in states.items()],
    })
