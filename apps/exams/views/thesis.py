"""The Abschlussarbeit: registering its topic and examiners."""
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.access import require
from core.models import Term
from core.shortcuts import without_term
from courses.forms import ThesisForm
from courses.models import Course, Participation
from curriculum.models import Module
from students.models import Study


@require('manage_exams')
def thesis(request, pk):
    """Register an Abschlussarbeit: its topic is given out today, due after the time the Prüfungsordnung allows."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'program', 'subject', 'start_term'), 'manage_exams'), pk=pk, status=Study.Status.ENROLLED)
    term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester, in dem das Thema ausgegeben wird')
    weeks = study.program.thesis_weeks
    course = Course(term=term, kind=Course.Kind.THESIS, subject=study.subject, deadline=timezone.localdate() + timedelta(weeks=weeks) if weeks else None)
    form = ThesisForm(request.POST or None, instance=course, study=study)
    valid = form.is_valid()
    if missing := unmet(study, form.cleaned_data['module'] if valid else None if form.is_bound else form.fields['module'].initial):
        messages.warning(request, f'Laut Plan noch nicht erfüllt: {"; ".join(missing)}.')
    if valid:
        module = form.cleaned_data['module']
        with transaction.atomic():
            course = form.save()
            course.modules.set([module])
            Participation.objects.create(course=course, study=study, module=module)
        study.notify(
            'abschlussarbeit', reverse('exams:index'), modul=module, thema=course.title, frist=f'{course.deadline:%d.%m.%Y}',
            gutachter=', '.join(map(str, course.staff.all())),
        )
        messages.success(request, 'Abschlussarbeit angemeldet. Bescheid versandt.')
        return redirect(course)
    return render(request, 'courses/form.html', {
        'form': form, 'course': course, 'menu': 'exams/_nav.html', 'heading': f'Abschlussarbeit {study.person}', 'back': study.get_absolute_url(),
    })


def unmet(study, module):
    """What the plan wants before `module`, on it or above it, and the study has not done: the credits (requires_credits)
    and the modules passed (requires)."""
    if module is None:
        return []
    chain, node = [], module
    while node:
        chain.append(node)
        node = node.parent
    tree, _ = study.progress()
    found, done = [], set()

    def walk(node):
        if node.done:
            done.add(node.pk)
        for child in node.nodes:
            walk(child)

    for root in tree:
        walk(root)
    if (credits := max(m.requires_credits or 0 for m in chain)) > (earned := sum(m.earned for m in tree)):
        found.append(f'{credits} CP, erworben sind {earned}')
    found += [f'{m.title} bestanden' for m in Module.objects.filter(required_by__in=chain).distinct() if m.pk not in done]
    return found
