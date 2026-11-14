"""Immatrikulation, Urlaubssemester and Exmatrikulation."""
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import dates
from core.access import require
from core.apps import plural
from core.models import Consent, Institution, Person, Term
from core.shortcuts import ids, preview
from core.text import handle
from admissions.papers import papers
from exams.results import exhausted
from setup.views import invitation
from ..forms import GuestForm
from ..leaving import leaving
from ..models import EXITS, FIRST_TERM_LEAVES, LEAVES, OFFICIAL, Document, DocumentType, Study, StudyTerm


def next_matriculation():
    """The next free Matrikelnummer, counting on from the first the Hochschule set."""
    numbers = [int(n) for n in Person.objects.exclude(matriculation=None).values_list('matriculation', flat=True) if n.isdigit()]
    return max(numbers, default=Institution.get().matriculation_start - 1) + 1


def matriculate(study):
    """Immatrikulation of an admitted applicant with nothing missing, the fee paid among it: Matrikelnummer, first
    registered term, Bescheid."""
    person, domain = study.person, Institution.get().student_mail
    with transaction.atomic():
        if not person.matriculation:
            person.matriculation = str(next_matriculation())
        if domain and not person.email.endswith(f'@{domain}'):  # a Hochschul address; the private one is kept for later
            person.private_email, name, n = person.private_email or person.email, handle(person.first_name, person.last_name), 1
            while Person.objects.filter(email=f'{name}{n if n > 1 else ""}@{domain}').exists():
                n += 1
            person.email = f'{name}{n if n > 1 else ""}@{domain}'
        person.save(update_fields=['matriculation', 'email', 'private_email'])
        StudyTerm.objects.get_or_create(study=study, term=study.start_term)
        study.set_status(Study.Status.ENROLLED)
    study.notify('immatrikulation', study.get_absolute_url())


@require('manage_students')
@require_POST
def enrol(request, pk):
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'start_term'), 'manage_students'), pk=pk, status=Study.Status.ADMITTED)
    if missing := study.missing():
        messages.error(request, f'Noch nicht immatrikuliert: {", ".join(missing)}.')
        return redirect(study.get_absolute_url())
    if not request.POST.get('bestaetigt'):
        wording = study.wording('immatrikulation', matrikelnummer=study.person.matriculation or next_matriculation())
        return preview(request, 'Immatrikulation', [('Immatrikulation', [study.person], wording)], 'Immatrikulieren und Bescheid versenden', study.get_absolute_url())
    matriculate(study)
    messages.success(request, f'{study.person} immatrikuliert, Matrikelnummer {study.person.matriculation}. Bescheid versandt.')
    return redirect(study.get_absolute_url())


@require('manage_students')
@require_POST
def enrol_many(request):
    """Immatrikulation of the ticked applicants whose places are taken up and complete."""
    studies = request.user.within(Study.objects.filter(pk__in=ids(request.POST.getlist('study')), status=Study.Status.ADMITTED), 'manage_students')
    ready = [s for s in papers(studies).select_related('subject', 'start_term').order_by('person__last_name') if not s.missing()]
    back = request.POST.get('next', '')
    back = back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else reverse('admissions:board')
    if not ready:
        messages.error(request, 'Niemand angekreuzt, für den alles vorliegt.')
    elif not request.POST.get('bestaetigt'):
        people = [f'{s.person.name} · {s.subject} · {s.program.degree}' for s in ready]
        wording = ready[0].wording('immatrikulation', matrikelnummer=ready[0].person.matriculation or next_matriculation())
        return preview(request, 'Immatrikulation', [('Immatrikulation', people, wording)], 'Immatrikulieren und Bescheide versenden', back)
    else:
        for study in ready:
            matriculate(study)
        messages.success(request, f'{plural(len(ready), "Person", "Personen")} immatrikuliert. Bescheide versandt.')
    return redirect(back)


@require('manage_students')
@require_POST
def checked(request, pk):
    """A Hochschulzugangsberechtigung from abroad is assessed and opens the study."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    study.person.entrance_checked = timezone.localdate()
    study.person.save(update_fields=['entrance_checked'])
    messages.success(request, 'Hochschulzugang als geprüft verbucht.')
    return redirect(study.get_absolute_url())


@require_POST
def leave(request, pk):
    """Urlaubssemester for the upcoming term. Students ask within the Rückmeldung, never afterwards, for at most the
    Hochschule's `leave_terms`, and in the first Fachsemester, where it says so, only for illness or Mutterschutz; the
    office decides beyond that."""
    study = get_object_or_404(Study, pk=pk, status=Study.Status.ENROLLED)
    office = request.user.may('manage_students', study)
    if not (study.person == request.user or office):
        raise PermissionDenied
    term = Term.upcoming()
    registration = StudyTerm.objects.filter(study=study, term=term).first()
    leave = not (registration and registration.leave)
    reason, institution = request.POST.get('reason', '') if leave else '', Institution.get()
    rule = leave and (  # what a student may ask for by themselves; the office decides beyond it, reminded
        f'Urlaubssemester für das {term} lassen sich vom {date_format(term.reregistration_start, "j.n.")} bis zum {date_format(term.closes, "j.n.Y")} beantragen.' if not office and not term.reregistration_start <= timezone.localdate() <= term.closes
        else f'In der Regel sind höchstens {institution.leave_terms} Urlaubssemester möglich; ein weiteres nur auf schriftlichen Antrag.' if study.terms.filter(leave=True).count() >= institution.leave_terms
        else 'Im ersten Fachsemester ist eine Beurlaubung nur bei Erkrankung oder Mutterschutz und Elternzeit möglich.' if institution.first_term_leave and not study.semester and reason not in FIRST_TERM_LEAVES
        else None
    )
    if leave and reason not in dict(LEAVES) or rule and not office:
        messages.error(request, rule if reason in dict(LEAVES) else 'Bitte den Grund der Beurlaubung wählen.')
        return redirect(study.get_absolute_url())
    if leave and (file := request.FILES.get('file')):  # an attest, say: for the student office only
        if refusal := DocumentType.system('attest').refuses(file) or (not request.POST.get('consent') and 'Ohne Einwilligung lässt sich der Nachweis nicht hochladen.'):
            messages.error(request, refusal)
            return redirect(study.get_absolute_url())
        Document.hand_in(study, DocumentType.system('attest'), file, Consent.give(study.person, 'zum Urlaubssemester', request.user))
    registration, _ = StudyTerm.objects.get_or_create(study=study, term=term)
    registration.leave, registration.leave_reason = leave, reason
    registration.part_time = registration.part_time and not leave  # a semester on leave is neither full nor part time
    registration.save(update_fields=['leave', 'leave_reason', 'part_time'])
    if leave:
        study.notify('beurlaubung', study.get_absolute_url(), rueckmeldesemester=term, grund=dict(LEAVES)[reason])
    else:
        study.notify('beurlaubung_aufgehoben', study.get_absolute_url(), rueckmeldesemester=term)
    messages.success(request, f'{term}: {"Urlaubssemester eingetragen" if leave else "Urlaubssemester zurückgenommen"}, Bescheid versandt.')
    if rule:
        messages.warning(request, f'Als Ausnahme eingetragen: {rule} Antrag und Nachweis gehören zur Akte.')
    return redirect(study.get_absolute_url())


@require('manage_students')
def guest(request):
    """Take on a Gasthörer:in or Zweithörer:in from their application: an account with a Hörernummer from the
    Matrikelnummern, the study, a Bescheid and an invitation to sign in. Courses they are entered in by name."""
    form = GuestForm(request.POST or None)
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            person = Person.objects.create_user(
                data['email'], data['email'], first_name=data['first_name'], last_name=data['last_name'], birth_date=data['birth_date'],
                matriculation=str(next_matriculation()),
            )
            study = Study.objects.create(
                person=person, program=data['program'], subject=data['subject'], start_term=Term.current(), status=data['status'],
                home_university=data['home_university'], home_matriculation=data['home_matriculation'],
            )
            if not request.user.may('manage_students', study):
                raise PermissionDenied('Diesen Studiengang verwaltet ein anderes Amt.')
        study.notify('gaststudium', study.get_absolute_url(), art=study.get_status_display())
        invitation(request, person)
        messages.success(request, f'{person} als {study.get_status_display()} aufgenommen, Nummer {person.matriculation}. Bescheid und Einladung versandt; Lehrveranstaltungen dort eintragen.')
        return redirect(study.get_absolute_url())
    return render(request, 'students/guest.html', {'form': form})


@require_POST
def waive(request, pk):
    """Lift the fees beyond the Semesterbeitrag for the upcoming term, on application (raising children, care, committee
    work, as the Land allows), or charge them again; with a Bescheid."""
    study = get_object_or_404(request.user.within(Study.objects.filter(status__in=Study.ATTENDING), 'manage_students'), pk=pk)
    term = Term.upcoming()
    registration, _ = StudyTerm.objects.get_or_create(study=study, term=term)
    StudyTerm.objects.filter(study__person=study.person, term=term).update(waived=not registration.waived)  # the fees are the person's
    study.notify('gebuehren_erlassen' if not registration.waived else 'gebuehren_erhoben', study.get_absolute_url(), rueckmeldesemester=term)
    messages.success(request, f'{term}: Gebühren {"erhoben" if registration.waived else "erlassen"}, Bescheid versandt.')
    return redirect(study.get_absolute_url())


@require_POST
def part_time(request, pk):
    """Teilzeit for the upcoming term, or full time again: the office enters it on the student's application, with a
    Bescheid; a semester on leave is neither."""
    study = get_object_or_404(request.user.within(Study.objects.filter(status=Study.Status.ENROLLED), 'manage_students'), pk=pk)
    if not Institution.get().part_time:
        raise PermissionDenied
    term = Term.upcoming()
    registration, _ = StudyTerm.objects.get_or_create(study=study, term=term)
    if registration.leave:
        messages.error(request, f'{term}: Urlaubssemester, also weder Voll- noch Teilzeit.')
        return redirect(study.get_absolute_url())
    registration.part_time = not registration.part_time
    registration.save(update_fields=['part_time'])
    study.notify('teilzeit' if registration.part_time else 'teilzeit_aufgehoben', study.get_absolute_url(), rueckmeldesemester=term)
    messages.success(request, f'{term}: {"Teilzeit eingetragen" if registration.part_time else "wieder Vollzeit"}, Bescheid versandt.')
    return redirect(study.get_absolute_url())


def leaving_day(study, lost):
    """The last day enrolled, unless asked otherwise: after a final fail the day the Bescheid became final, or the day
    before this semester if that was earlier; after the Abschluss the end of the semester; otherwise today."""
    term = Term.current()
    if lost:
        return max(max(m.appeal for m in lost) + timedelta(days=1), term.start - timedelta(days=1))
    return term.end if study.issued else timezone.localdate()


@require('manage_students')
@require_POST
def exmatriculate(request, pk):
    """Exmatrikulation with the last day enrolled: on request the day asked for, up to the end of the semester; after the
    Abschluss the end of the semester; after a final fail once that is final; otherwise today; the day before this
    semester for someone who should not be in it. Semesters after that day are dropped, a fee paid for them is refunded
    as the text block `erstattung` says. The Bescheid first as a preview, with what is still open (the Laufzettel)."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'program', 'subject', 'start_term'), 'manage_students'), pk=pk, status=Study.Status.ENROLLED)
    reason, term, asked = request.POST.get('reason', ''), Term.current(), request.POST.get('zum', '').strip()
    on, earliest = dates.day(asked) if asked else term.end if reason == '1' else timezone.localdate(), term.start - timedelta(days=1)
    lost = exhausted(study) if reason == '8' else []
    if reason not in dict(EXITS):
        messages.error(request, 'Bitte den Grund der Exmatrikulation wählen.')
    elif reason == '8' and (not lost or max(m.appeal for m in lost) >= timezone.localdate()):  # only on the evidence, and once it is final
        messages.error(request, 'Kein endgültig nicht bestandenes Modul.' if not lost else f'Erst nach der Widerspruchsfrist, ab dem {date_format(max(m.appeal for m in lost) + timedelta(days=1), "j.n.Y")}.')
    elif not on or not earliest <= on <= term.end:
        messages.error(request, f'Bitte einen Tag vom {date_format(earliest, "j.n.Y")} bis zum {date_format(term.end, "j.n.Y")} angeben.')
    else:
        still, later = leaving(study), study.terms.filter(term__start__gt=on)
        paid = [r.term for r in later.select_related('term') if r.paid]
        after = ['erstattung'] * bool(paid) + ['auslaenderbehoerde'] * study.person.permit + ['rechtsbehelf'] * (reason in OFFICIAL)
        insurer = study.person.insurer and f'Die {study.person.insurer} erhält die Meldung der Exmatrikulation im Studierendenmeldeverfahren.'
        values = {'grund': dict(EXITS)[reason], 'zum': date_format(on, 'j. F Y'), 'rueckmeldesemester': ', '.join(map(str, paid))}
        if not request.POST.get('bestaetigt'):
            notes = [f'Noch offen: {what}{f" ({who})" if who else ""}' for what, who in still] + [insurer] * bool(insurer)
            return preview(request, f'Exmatrikulation, letzter Tag {date_format(on, "j.n.Y")}', [('Exmatrikulation', [study.person], study.wording('exmatrikulation', *after, **values))], 'Exmatrikulieren und Bescheid versenden', study.get_absolute_url(), notes)
        with transaction.atomic():
            later.filter(paid=None).delete()
            later.update(withdrawn=True)  # what was paid stays on record, for its refund
            study.set_status(Study.Status.GRADUATED if reason == '1' else Study.Status.LEFT, reason, on)
        study.notify('exmatrikulation', after=after, **values)
        messages.success(request, f'{study.person} mit Ablauf des {date_format(on, "j.n.Y")} exmatrikuliert. Bescheid versandt.' + (f' {insurer}' if insurer else '') + (f' Noch offen: {"; ".join(f"{what} ({who})" if who else what for what, who in still)}.' if still else ''))
    return redirect(study.get_absolute_url())
