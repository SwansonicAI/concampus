"""Bescheide sent and printed, Bescheinigungen, and checking one is genuine."""
import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_not_required
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from lxml.etree import DocumentInvalid

from core import texts
from core.access import require
from core.apps import plural
from core.models import Term
from core.shortcuts import SHOWN
from core.text import matching
from .. import xhochschule
from ..models import Document, Study, StudyTerm

DELIVERY = {'post': 'per Post, noch nicht gedruckt', 'gedruckt': 'per Post, gedruckt', 'elektronisch': 'elektronisch'}


@require('manage_students')
def notices(request):
    """Versandte Bescheide: every Bescheid the office's studies received, newest first, how each reached its person; by
    the text it came from and how it is delivered. Those going by post print all at once, each a letter, and are marked
    printed afterwards."""
    q, kind, delivery = request.GET.get('q', '').strip(), request.GET.get('art', ''), request.GET.get('zustellung', '')
    sent = (
        Document.objects.filter(kind='notice', study__in=request.user.within(Study.objects.all(), 'manage_students'))
        .select_related('person', 'study__program', 'study__subject', 'message').order_by('-created')
    )
    kinds = sorted(((k, texts.TEXTS[k].title) for k in set(sent.exclude(template='').values_list('template', flat=True)) if k in texts.TEXTS), key=lambda k: k[1])
    if kind:
        sent = sent.filter(template=kind)
    by_post = Q(person__electronic=None)
    sent = sent.filter({'post': by_post & Q(printed=None), 'gedruckt': by_post & Q(printed__isnull=False), 'elektronisch': ~by_post}.get(delivery, Q()))
    sent = matching(sent, q, 'person__first_name', 'person__last_name', 'person__matriculation', starts=True)
    if 'drucken' in request.GET:
        shown = list(sent[:SHOWN])
        return render(request, 'core/letter.html', {
            'letters': [d.message for d in shown if d.message], 'printing': [d.pk for d in shown if not (d.person.electronic or d.printed)],
        })
    return render(request, 'students/notices.html', {
        'sent': sent[:SHOWN], 'count': sent.count(), 'kinds': kinds, 'kind': kind, 'q': q, 'deliveries': DELIVERY, 'delivery': delivery,
    })


@require('manage_students')
@require_POST
def printed(request):
    """Bescheide by post marked printed, after printing them all."""
    done = Document.objects.filter(
        pk__in=request.POST.getlist('bescheid'), kind='notice', printed=None, study__in=request.user.within(Study.objects.all(), 'manage_students'),
    ).update(printed=timezone.now())
    messages.success(request, f'{plural(done, "Bescheid", "Bescheide")} als gedruckt markiert.')
    return redirect(f"{reverse('students:notices')}?zustellung=post")


def periods(request, pk):
    """Studienzeitbescheinigung: every semester the person was registered here, on leave or not, and how each study ended;
    asked for by pension insurance long after."""
    study = get_object_or_404(Study.objects.select_related('person'), pk=pk)
    person = study.person
    if not (person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    today = timezone.localdate()
    studies = list(person.studies.filter(terms__isnull=False).distinct().select_related('program', 'subject').order_by('start_term__start'))
    rows = []
    for s in studies:
        semester = s.prior_terms
        for registration in s.terms.filter(term__start__lte=today, withdrawn=False).select_related('term'):
            semester += not registration.leave
            rows.append((registration.term, s, registration, semester))
    rows.sort(key=lambda row: row[0].start)
    if 'xml' in request.GET:
        return data(request, lambda: xhochschule.periods(study, rows, first_enrolment(person)), f'studienzeitbescheinigung-{person.matriculation}')
    return render(request, 'students/periods.html', {
        'study': study, 'person': person, 'studies': studies, 'rows': rows, 'count': len({row[0] for row in rows}),
        'leave': len({term for term, _, registration, _ in rows if registration.leave}), 'today': today, 'sender': texts.block('absender_bescheinigungen'),
    })


def exmatriculation(request, pk):
    """Exmatrikulationsbescheinigung: when someone studied here and why it ended, as health insurers and job centres ask."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk, status__in=Study.FORMER)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    if 'xml' in request.GET:
        return data(request, lambda: xhochschule.exmatriculation(study, first_enrolment(study.person)), f'exmatrikulationsbescheinigung-{study.person.matriculation}')
    return render(request, 'students/exmatriculation.html', {
        'study': study, 'today': timezone.localdate(), 'sender': texts.block('absender_bescheinigungen'),
        'reason': study.exit_reason == '1' or request.GET.get('grund'),  # often handed on: the ground only where asked for
    })


def certificate(request, pk):
    """Immatrikulationsbescheinigung for the current term; with ?bafoeg the version of § 9 BAföG, which stands in for
    Formblatt 2: what its part C asks of a Hochschule besides."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk, status=Study.Status.ENROLLED)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    term, coming = Term.current(), Term.upcoming()  # the coming semester too, once its Rückmeldung is paid: for a BAföG Folgeantrag
    ahead = coming and request.GET.get('semester') == str(coming.pk) and study.terms.filter(term=coming, paid__isnull=False).first()
    term, first = coming if ahead else term, first_enrolment(study.person)
    registration, fs, code = study.terms.filter(term=term).first(), study.semester + (bool(ahead) and not ahead.leave), f'{study.pk}-{term.pk}-{signature(study.pk, term.pk)}'
    check = request.build_absolute_uri(reverse('students:verify'))
    if 'xml' in request.GET:
        return data(
            request, lambda: xhochschule.enrolment(study, term, registration, fs, first, (code, f'{check}?code={code}'), 'bafoeg' in request.GET),
            f'immatrikulationsbescheinigung-{study.person.matriculation}-{slugify(term.name)}',
        )
    return render(request, 'students/certificate.html', {
        'study': study, 'term': term, 'registration': registration, 'today': timezone.localdate(), 'fs': fs, 'hs': study.university_semester + bool(ahead),
        'bafoeg': 'bafoeg' in request.GET, 'first': first, 'sender': texts.block('absender_bescheinigungen'), 'code': code, 'check': check,
    })


def first_enrolment(person):
    """The semester of the first enrolment anywhere: as the record names one elsewhere, else the first registered here."""
    first = StudyTerm.objects.filter(study__person=person, withdrawn=False).order_by('term__start').select_related('term').first()
    return person.first_term or (first and first.term)


def data(request, build, name):
    """A Bescheinigung as an XHochschule file to download; where the record lacks what the standard asks, back to the page
    with what is missing."""
    try:
        content = build()
    except DocumentInvalid as error:
        messages.error(request, f'Als XHochschule-Datei noch nicht möglich, in der Akte fehlt etwas: {error}')
        return redirect(request.path)
    response = HttpResponse(content, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{name}.xml"'
    return response


def signature(study, term):
    """The Prüfcode of an Immatrikulationsbescheinigung: nobody makes one up for another study or semester."""
    return salted_hmac('immatrikulationsbescheinigung', f'{study}-{term}').hexdigest()[:10].upper()


@login_not_required
def verify(request):
    """Whether an Immatrikulationsbescheinigung is genuine, for whoever it was handed to: its Prüfcode names the study
    and the semester. It shows what the Bescheinigung says, nothing more."""
    code, study, term = request.GET.get('code', '').strip().upper(), None, None
    if found := re.fullmatch(r'(\d+)-(\d+)-([0-9A-F]{10})', code):
        if constant_time_compare(found[3], signature(found[1], found[2])):
            study = Study.objects.select_related('person', 'program', 'subject').filter(pk=found[1]).first()
            term = Term.objects.filter(pk=found[2]).first()
    return render(request, 'students/verify.html', {
        'code': code, 'study': study, 'term': term, 'registered': bool(study and term and study.terms.filter(term=term, study__status__in=[Study.Status.ENROLLED, *Study.FORMER]).exists()),
    })
