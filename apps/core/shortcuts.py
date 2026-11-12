"""Responses many apps' views give: a confirmation before Bescheide go out, a page for work that needs a semester, a calendar."""
from datetime import UTC

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Institution

SHOWN = 300  # rows a long list shows; filters narrow it, exports have all


def preview(request, title, groups, send, back, notes=()):
    """Bescheide before they go out, or a choice before it holds: who gets which Bescheid, one of each in full, and what
    to know first. Sending posts the same form again, confirmed (`bestaetigt`). `groups`: (heading, the people, (subject,
    body) of one Bescheid)."""
    fields = [(name, value) for name in request.POST if name not in ('csrfmiddlewaretoken', 'bestaetigt') for value in request.POST.getlist(name)]
    return render(request, 'core/preview.html', {'title': title, 'groups': groups, 'send': send, 'back': back, 'notes': notes, 'fields': fields})


def without_term(request, what):
    """Where work needs a semester nobody has created yet."""
    return render(request, 'core/no_term.html', {'what': what})


def ics(request, events, filename=None):
    """iCalendar of (uid, start, end, summary, location) for any calendar app; with a filename, to download."""

    def text(value):
        return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')

    def stamp(moment):
        return moment.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')

    name, host, now = Institution.get().name, request.get_host().split(':')[0], timezone.now()
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:-//{text(name)}//ConCampus//DE', f'X-WR-CALNAME:{text(name)}']
    for uid, start, end, summary, location in events:
        lines += [
            'BEGIN:VEVENT', f'UID:{uid}@{host}', f'DTSTAMP:{stamp(now)}', f'DTSTART:{stamp(start)}', f'DTEND:{stamp(end)}',
            f'SUMMARY:{text(summary)}', f'LOCATION:{text(location)}', 'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    response = HttpResponse('\r\n'.join(lines) + '\r\n', content_type='text/calendar; charset=utf-8')
    if filename:
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
