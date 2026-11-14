"""Responses many apps' views give: a confirmation before Bescheide go out, a page for work that needs a semester, a calendar,
a file a player may ask for in parts."""
import csv
import re
from datetime import UTC

from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Institution

SHOWN = 300  # rows a long list shows; filters narrow it, exports have all
FORMULA, NUMBER = ('=', '+', '-', '@', '\t', '\r'), re.compile(r'-?[\d.,]+')  # what Excel takes for a formula; a number
RANGE = re.compile(r'bytes=(\d+)-(\d*)')  # one span of a file, as players ask for it


def preview(request, title, groups, send, back, notes=()):
    """Bescheide before they go out, or a choice before it holds: who gets which Bescheid, one of each in full, and what
    to know first. Sending posts the same form again, confirmed (`bestaetigt`). `groups`: (heading, the people, (subject,
    body) of one Bescheid)."""
    fields = [(name, value) for name in request.POST if name not in ('csrfmiddlewaretoken', 'bestaetigt') for value in request.POST.getlist(name)]
    return render(request, 'core/preview.html', {'title': title, 'groups': groups, 'send': send, 'back': back, 'notes': notes, 'fields': fields})


def ids(values):
    """The record ids among submitted values (getlist): a box ticked, never something typed in that breaks a query."""
    return [int(v) for v in values if v.isdigit()]


class Sheet:
    """A CSV writer for files opened in Excel: a cell that would start a formula (=, +, -, @) begins with ' and stays
    text, so a name like „=HYPERLINK(…)“ runs nothing (OWASP, CSV injection); negative numbers stay numbers."""

    def __init__(self, out):
        self.writer = csv.writer(out, delimiter=';')

    def writerow(self, row):
        self.writer.writerow([f"'{v}" if isinstance(v, str) and v[:1] in FORMULA and not NUMBER.fullmatch(v) else v for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def ranged(request, file, **kwargs):
    """A FileResponse that also answers `Range: bytes=…` with just those bytes: browsers ask so to play a video from any
    point, a jury from where the Prüfungsprogramm says, without loading all that comes before."""
    response = FileResponse(file, **kwargs)
    response['Accept-Ranges'] = 'bytes'
    match, size = RANGE.fullmatch(request.headers.get('Range', '')), file.size
    if match and (start := int(match[1])) <= (end := min(int(match[2] or size - 1), size - 1)):

        def span(left=end - start + 1):
            file.seek(start)
            while left > 0 and (chunk := file.read(min(left, 1 << 16))):
                left -= len(chunk)
                yield chunk

        response.streaming_content = span()  # the file is still closed with the response
        response.status_code = 206
        response['Content-Range'], response['Content-Length'] = f'bytes {start}-{end}/{size}', end - start + 1
    return response


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
