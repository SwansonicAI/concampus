"""Aussonderung: offering former students' files to the archive and handing them over."""
from collections import Counter

from django.contrib import messages
from django.db.models import Count, Max, Prefetch, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.access import require
from core.dossier import dossier
from core.apps import plural
from core.models import Export, Institution, Person
from core.shortcuts import SHOWN
from ..disposal import assess, due, offer
from ..models import Disposal, Study


@require('dispose_files', 'assess_files')
def disposal(request):
    """Aussonderung (students/disposal.py): the student office offers the files kept long enough to the archive; the
    archive takes over a copy of those worth keeping and assesses the rest as not; the night after, each file assessed
    is cut down to its Grunddaten."""
    roles = request.user.roles
    if request.method == 'POST':
        if request.POST.get('anbieten') and 'dispose_files' in roles:
            messages.success(request, f'{plural(offer(due(), request.user), "Akte", "Akten")} dem Archiv angeboten.')
        elif request.POST.get('zuruecknehmen') and 'assess_files' in roles:  # until the night cuts them down
            n = Disposal.objects.filter(assessed__isnull=False, reduced=None).update(assessed=None, archival=None, assessed_by=None)
            messages.success(request, f'{plural(n, "Bewertung", "Bewertungen")} zurückgenommen; die Akten sind wieder angeboten.')
        elif (archival := request.POST.get('archivwuerdig')) and 'assess_files' in roles:
            if n := assess(request.POST.getlist('person'), archival == 'ja', request.user):
                messages.success(request, f'{plural(n, "Akte", "Akten")} bewertet; heute Nacht werden sie bis auf die Grunddaten gelöscht.')
            else:
                messages.error(request, 'Bitte die Akten ankreuzen, die bewertet werden.')
        return redirect('students:disposal')
    last = {p.pk: p.last for p in due().annotate(last=Max('studies__since'))}
    done = Disposal.objects.exclude(assessed=None).values('assessed__year').annotate(n=Count('pk'), kept=Count('pk', filter=Q(archival=True))).order_by('-assessed__year')
    offered = Person.objects.filter(disposal__isnull=False, disposal__assessed=None).select_related('disposal').prefetch_related(
        Prefetch('studies', Study.objects.filter(status__in=Study.FORMER).select_related('program', 'subject', 'start_term')),
    ).order_by('last_name', 'first_name')
    return render(request, 'students/disposal.html', {
        'due': sorted(Counter(day.year for day in last.values()).items()), 'due_total': len(last), 'offered': offered[:SHOWN], 'offered_total': offered.count(),
        'done': done, 'institution': Institution.get(), 'assessing': 'assess_files' in roles, 'offering': 'dispose_files' in roles,
        'pending': Disposal.objects.filter(assessed__isnull=False, reduced=None).select_related('person'),
    })


@require('assess_files')
def handover(request, pk):
    """A copy of an offered file for the archive to take over."""
    person = get_object_or_404(Person, pk=pk, disposal__reduced=None)
    Export.log(request.user, f'Akte {person} für das Archiv', 1)
    return FileResponse(dossier(person), as_attachment=True, filename=f'akte-{person.matriculation or person.pk}.zip')
