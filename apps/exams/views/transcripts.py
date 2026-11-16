"""Leistungsübersicht, Zeugnis, Urkunde and Diploma Supplement."""
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import paper, texts
from core.models import Message, Office, Person
from students.leaving import leaving
from students.models import Study
from students.trail import above
from ..results import achievements, overdue, reminded

GRADES = [(1.5, 'sehr gut', 'very good'), (2.5, 'gut', 'good'), (3.5, 'befriedigend', 'satisfactory'), (4.0, 'ausreichend', 'sufficient')]


def verbal(grade, english=False):
    """German grade in words, as on a Zeugnis, or as the Diploma Supplement translates it."""
    for limit, word, translated in GRADES:
        if grade <= limit:
            return translated if english else word
    return 'fail' if english else 'nicht ausreichend'


def transcript(request, pk):
    """Leistungsübersicht or, once all credits are earned, Zeugnis."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'focus'), pk=pk)
    if not (study.person == request.user or request.user.may('view_files', study)):
        raise PermissionDenied
    found, office = achievements(study), request.user.may('decide_results', study)  # a Zeugnis comes from the office
    copy = not office and study.person == request.user and study.issued and found['finished']  # graduates print their own
    late = not office and found['finished'] and study.issued is None and request.user.may('correct_scores', study) and overdue(found)  # theirs to remind
    return paper.answer(request, 'exams/transcript.html', found | {
        'trail': [(None, request.path)] if study.person == request.user else above(request.user, study),  # one's own: a page of Mein Studium
        'complete': found['finished'] and (office or copy), 'copy': copy, 'supplement': (office or copy) and study.program.level,
        'issue': office and found['finished'] and study.issued is None and study.status == Study.Status.ENROLLED,
        'still': [item for item in leaving(study) if not item[0].startswith('Zeugnis')] if office and found['finished'] and study.issued is None else [],
        'verbal': verbal(found['average']) if found['average'] else '', 'sender': texts.block('absender_leistungen'),
        'late': late, 'reminded': late and reminded(study), 'issuer': late and Office.holding('decide_results', program=study.program, linked=True),
    }, f'{"Zeugnis" if found["finished"] and (office or copy) else "Leistungsübersicht"} {study.person.name}')


@require_POST
def remind(request, pk):
    """Remind whoever issues the study's Zeugnis that it is long overdue; for who corrects results, once a week."""
    study = get_object_or_404(Study.objects.select_related('person', 'program'), pk=pk, status=Study.Status.ENROLLED, issued=None)
    if not request.user.may('correct_scores', study):
        raise PermissionDenied
    due = overdue(achievements(study))
    if due and not reminded(study):
        values = {'studierende': study.person, 'studiengang': study.program.title, 'frist': f'{due:%d.%m.%Y}'}
        for p in Person.holding('decide_results'):
            if p.may('decide_results', study):
                Message.send(p, *texts.render('zeugnis_ueberfaellig', vorname=p.first_name, nachname=p.last_name, **values), reverse('exams:transcript', args=[study.pk]))
        messages.success(request, 'Erinnert.')
    return redirect('exams:transcript', study.pk)


@require_POST
def issue(request, pk):
    """The Zeugnis issued once every credit is earned: noted in the file and told to the student; the Exmatrikulation follows."""
    study = get_object_or_404(Study.objects.select_related('person', 'program'), pk=pk, status=Study.Status.ENROLLED, issued=None)
    if not request.user.may('decide_results', study):
        raise PermissionDenied
    if not achievements(study)['finished']:
        messages.error(request, 'Es fehlen noch Leistungen.')
    else:
        study.issued, study.grade = timezone.localdate(), achievements(study)['average']
        study.save(update_fields=['issued', 'grade'])
        texts.send('zeugnis', study.person, reverse('exams:transcript', args=[study.pk]), studiengang=study.program.title)
        messages.success(request, f'Zeugnis ausgestellt. Die Exmatrikulation steht an: {Office.holding("manage_students") or "Studierendenverwaltung"}.')
    return redirect('exams:transcript', study.pk)


def certificate(request, pk):
    """The Urkunde conferring the degree, once the Zeugnis is issued: always as its PDF, which its Briefbogen may set from
    its placeholders and Textfelder alone (core.paper.SET), so no page could say otherwise."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk, issued__isnull=False)
    office = request.user.may('decide_results', study)
    if not (office or study.person == request.user):
        raise PermissionDenied
    name = f'Urkunde {study.person.name}'
    return paper.respond(paper.pdf(render_to_string('exams/certificate.html', {
        'study': study, 'completed': achievements(study)['completed'], 'copy': not office, 'signature': texts.block('unterschrift_urkunden'),
    }, request), name), name)


def supplement(request, pk):
    """The Diploma Supplement to the Zeugnis: in English, in the sections of the European model. For the office a draft until
    every credit is earned; for graduates a copy of their own."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk)
    office = request.user.may('decide_results', study)
    if not (office or study.person == request.user and study.issued):
        raise PermissionDenied
    if not study.program.level:
        raise Http404
    found, de = achievements(study), request.GET.get('sprache') == 'de'  # in English and German
    if not (office or found['finished']):
        raise Http404
    bands, reference = distribution(study.program)
    return paper.answer(request, 'exams/supplement.html', found | {
        'copy': not office, 'de': de, 'lang': 'de' if de else 'en',
        'verbal': verbal(found['average'], english=not de) if found['average'] else '', 'grades': GRADES,
        'awarding': texts.block('ds_hochschule'), 'system': texts.block('ds_hochschulsystem_de' if de else 'ds_hochschulsystem'),
        'distribution': bands, 'reference': reference,
        'dated': found['completed'] if found['finished'] and found['completed'] else found['today'],  # the date of the Zeugnis
    }, f'Diploma Supplement {study.person.name}')


def distribution(program):
    """The ECTS grading table of a Studiengang: how the Gesamtnoten of its graduates in the three years before spread over
    the grades, in percent; ([(limit, word, translated, share)], how many), the table empty when fewer than ten."""
    since = timezone.localdate() - timedelta(days=3 * 365)
    grades = list(Study.objects.filter(
        status=Study.Status.GRADUATED, program__degree=program.degree, program__name=program.name, since__gte=since, grade__isnull=False,
    ).values_list('grade', flat=True))
    if len(grades) < 10:
        return [], len(grades)
    bands, below = [], 0
    for limit, word, translated in GRADES:
        bands.append((limit, word, translated, round(100 * sum(1 for g in grades if below < g <= limit) / len(grades))))
        below = limit
    return bands, len(grades)
