from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from courses.models import Course
from curriculum.models import Module, Program
from rooms.models import Booking, BookingCategory, Room
from rooms.views import agenda
from students.models import Study
from . import dates
from .apps import feature, tile_apps, tiles
from .days import busy, strip
from .history import describe, restore, tables
from .models import Change, Message, Person, Term, Tracked
from .text import fold, matching, words


def home(request):
    person, today = request.user, timezone.localdate()
    day = dates.day(request.GET.get('tag', '')) or today
    bookings = agenda(person)
    marked = busy(bookings, today)
    apps = tiles(person)
    tasks = sorted(
        ({**t, 'app': title} for app, title in apps for t in app.tasks(person)),
        key=lambda t: (t['due'] is None, t['due'] or today),
    )
    return render(request, 'core/home.html', {
        'term': Term.current(),
        'today': today,
        'soon': today + timedelta(days=7),
        'tasks': tasks,
        'tiles': [{'app': app, 'title': title, 'figure': app.figure(person)} for app, title in apps],
        'day': day,
        'calendar': bool(marked),
        'days': strip(today, day, marked),
        'agenda': bookings.filter(start__date=day).select_related('room', 'course', 'category').prefetch_related('participants'),
        'letters': person.messages.filter(read=None)[:3],
    })


def inbox(request):
    return render(request, 'core/inbox.html', {'letters': request.user.messages.all()})


ROLES = {'student': 'Studierende', 'applicant': 'Bewerbende', 'teacher': 'Lehrende'}


def demo_people():
    """The demo's people in the configured order, each with what they stand for: their office, else their role."""
    found = Person.objects.prefetch_related('groups').in_bulk(settings.DEMO, field_name='username')
    shown = []
    for username in settings.DEMO:
        if person := found.get(username):
            offices = person.groups.all()
            shown.append((offices[0].name if offices else next((label for role, label in ROLES.items() if role in person.roles), ''), person))
    return shown


@login_not_required
@require_POST
def demo(request):
    """Only in the demo: sign in as one of its people, without a password."""
    if request.POST.get('konto') not in settings.DEMO:
        raise Http404
    login(request, get_object_or_404(Person, username=request.POST['konto']), backend=settings.AUTHENTICATION_BACKENDS[0])
    return redirect('home')


def message(request, pk):
    letter = get_object_or_404(Message, pk=pk, person=request.user)
    if not letter.read:
        letter.read = timezone.now()
        letter.save(update_fields=['read'])
    place = letter.link and dict((app.label, title) for app, title in tiles(request.user)).get(resolve(urlsplit(letter.link).path).app_name)
    return render(request, 'core/message.html', {'letter': letter, 'place': place or 'Campus'})


def letter(request, pk):
    """A Bescheid from the Postfach as a printable letter, for its recipient and the office managing students."""
    letter = get_object_or_404(Message.objects.select_related('person'), pk=pk)
    if not (letter.person == request.user or 'manage_students' in request.user.roles):
        raise PermissionDenied
    return render(request, 'core/letter.html', {'letters': [letter]})


SHOWN = 300  # rows a long list shows; filters narrow it, exports have all


def without_term(request, what):
    """Where work needs a semester nobody has created yet."""
    return render(request, 'core/no_term.html', {'what': what})


def history(request, table, row):
    """A record's history, for the panel; a person's is everything about them. Each office sees the changes to the
    records it looks after, and may undo them."""
    visible = tables(request.user.roles)
    if not visible:
        raise PermissionDenied
    try:
        model = apps.get_model(table)
    except (LookupError, ValueError):
        raise Http404
    if not issubclass(model, Tracked):
        raise Http404
    changes = Change.objects.filter(model.history(row), table__in=visible).select_related('by')[:200]
    return render(request, 'core/history.html', {'record': model.objects.filter(pk=row).first(), 'changes': describe(changes)})


@require_POST
def undo(request, pk):
    change = get_object_or_404(Change, pk=pk)
    if not apps.get_model(change.table).visible_to(request.user.roles):
        raise PermissionDenied
    try:
        restore(change)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Wiederhergestellt.' if change.action == Change.Action.DELETED else 'Zurückgesetzt.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else 'home')


def when(request):
    """What a date field understood, for the hint next to it."""
    text = request.GET.get('q', '')
    found = (dates.day(text), None) if request.GET.get('nur') == 'tag' else dates.moment(text)
    if not found or not found[0]:
        return JsonResponse({})
    day, time = found
    return JsonResponse({'iso': day.isoformat(), 'text': date_format(day, 'l, j. F Y') + (f', {time:%H:%M} Uhr' if time else '')})


def search(request):
    """One box for everything: places in the apps, dates, and every record the person may see."""
    q = request.GET.get('q', '').strip()
    fragment = request.headers.get('X-Fragment')  # the list that opens under the search box
    groups = [(label, hits) for label, hits in found(request.user, q, 6 if fragment else 30) if hits]
    return render(request, 'core/_found.html' if fragment else 'core/search.html', {'q': q, 'groups': groups})


def hit(title, url, meta=''):
    return {'title': title, 'url': url, 'meta': meta}


def found(person, q, limit):
    roles, sought = person.roles, words(q)
    groups = []
    if q and roles - {'applicant'} and (when := dates.moment(q)):
        groups.append(('Springen zu', jumps(roles, *when)))
    # every app's places, also of apps without a tile for this person: a student applying for a Master
    places = [(f, app.title(roles) or app.verbose_name) for app in tile_apps() for f in app.features(person)]
    places += [(feature('Startseite', reverse('home'), 'aufgaben zu erledigen übersicht'), ''), (feature('Postfach', reverse('inbox'), 'nachrichten bescheide mitteilungen'), '')]
    if person.is_superuser:
        places.append((feature('Datenpflege', reverse('admin:index'), 'admin stammdaten verwaltung'), ''))
    groups.append(('Funktionen', [
        hit(f['title'], f['url'], app) for f, app in places if all(w in fold(f"{f['title']} {f['words']} {app}") for w in sought)
    ][:limit if q else None]))  # an empty box lists everything the person can do
    if len(q) < 2:
        return groups
    if roles & {'view_files', 'view_applications', 'teacher'}:  # only the files and applications the person may open
        seen = Q(teacher=person) & ~Q(status__in=Study.APPLICATION) if 'teacher' in roles else Q(pk=None)
        if 'view_files' in roles:
            seen |= ~Q(status__in=Study.APPLICATION)
        if 'view_applications' in roles:
            seen |= Q(status__in=Study.APPLICATION)
        studies = matching(Study.objects.filter(seen).select_related('person', 'program', 'subject'), q, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__matriculation', 'person__email')[:limit]
        groups.append(('Studierende und Bewerbungen', [
            hit(s.person.name, s.get_absolute_url(), ' · '.join(filter(None, [s.person.matriculation, f'{s.subject} {s.program.degree}', s.get_status_display()])))
            for s in studies
        ]))
    teaching = [Course.Kind.COURSE, Course.Kind.LESSON]
    courses = matching(Course.objects.filter(term__end__gte=timezone.localdate()).select_related('term'), q, 'title', 'staff__last_name', 'subject__name')
    groups.append(('Lehrveranstaltungen', [hit(c.title, c.get_absolute_url(), f'{c.get_kind_display()} · {c.term}') for c in courses.filter(kind__in=teaching)[:limit]]))
    papers = Q(staff=person)  # exams and auditions: those the person examines, all for the offices running them
    if roles & {'manage_exams', 'decide_results', 'recognise'}:
        papers |= Q(kind__in=[Course.Kind.EXAM, Course.Kind.RECOGNITION])
    if 'run_auditions' in roles:
        papers |= Q(kind=Course.Kind.AUDITION)
    exams = courses.exclude(kind__in=teaching).filter(papers).distinct()
    groups.append(('Prüfungen', [hit(c.title, c.get_absolute_url(), f'{c.get_kind_display()} · {c.term}') for c in exams[:limit]]))
    if roles & {'view_curriculum', 'teacher'}:
        groups.append(('Studiengänge', [hit(str(p), p.get_absolute_url()) for p in matching(Program.objects.all(), q, 'name', 'degree', names='studiengang studienplan')[:limit]]))
        groups.append(('Module', [
            hit(f'{m.code} {m.title}', m.program.get_absolute_url(), str(m.program))
            for m in matching(Module.objects.select_related('program'), q, 'code', 'title', names='modul')[:limit]
        ]))
    if roles - {'applicant'}:
        groups.append(('Räume', [
            hit(r.name, reverse('rooms:room', args=[r.pk]), ' · '.join(filter(None, [r.purpose, r.building, f'{r.capacity} Plätze'])))
            for r in matching(Room.objects.all(), q, 'name', 'purpose', 'building', 'equipment', names='raum')[:limit]
        ]))
        # bookings by title or category: concerts for everyone, one's own dates, everything for the offices
        shown = Booking.objects.filter(end__gte=timezone.now()).select_related('room', 'course', 'category')
        if 'view_bookings' not in roles:
            shown = shown.filter(Q(category__public=True) | Q(pk__in=agenda(person).values('pk')))
        named = [c.key for c in BookingCategory.objects.all() if all(w in fold(c.name) for w in sought)]
        events = shown.filter(Q(pk__in=matching(shown, q, 'title', 'description', 'course__title').values('pk')) | Q(category__in=named))
        groups.append(('Termine', [
            hit(b.label, reverse('rooms:booking', args=[b.pk]), f'{date_format(timezone.localtime(b.start), "D j.n., H:i")} · {b.room} · {b.category}')
            for b in events[:limit]
        ]))
    groups.append(('Postfach', [hit(m.subject, m.get_absolute_url(), date_format(m.created, 'j.n.Y')) for m in matching(person.messages.all(), q, 'subject', 'body')[:limit]]))
    return groups


def jumps(roles, day, time):
    """Where a typed date can take a person: the room plan, free rooms at that time, the week's timetable."""
    label = date_format(day, 'l, j. F Y')
    found = [hit(f'Raumplan am {label}', f"{reverse('rooms:index')}?tag={day:%Y-%m-%d}", 'Räume')]
    if time:
        end = (datetime.combine(day, time) + timedelta(hours=1)).time()
        found.append(hit(f'Freie Räume am {label}, {time:%H:%M}–{end:%H:%M} Uhr', f"{reverse('rooms:find')}?day={day:%d.%m.%Y}&start={time:%H:%M}&end={end:%H:%M}", 'Räume'))
    if roles & {'teacher', 'student'}:
        found.append(hit(f'Stundenplan, Woche vom {date_format(day - timedelta(days=day.weekday()), "j. F")}', f"{reverse('courses:timetable')}?tag={day:%Y-%m-%d}", 'Lehre'))
    return found
