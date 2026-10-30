from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, Exists, F, OuterRef, Q, When
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from courses.models import Course, Participation
from curriculum.models import Module, Program
from rooms.models import Booking, BookingCategory, Room
from rooms.views import agenda, moves
from students.models import Document, Study
from . import dates
from .apps import feature, plural, tile_apps, tiles
from .days import busy, strip
from .forms import ContactForm
from .history import because, describe, former, restore, tables
from .dates import months_before
from .keys import DISTRICTS, replaced
from .models import Change, Consent, Institution, Key, Message, Office, Person, Term, Tracked, limited
from .text import fold, matching, words


def home(request):
    person, today = request.user, timezone.localdate()
    day = dates.day(request.GET.get('tag', '')) or today
    bookings = agenda(person)
    marked = busy(bookings, today)
    shown = list(bookings.filter(start__date=day).select_related('room', 'course', 'category').prefetch_related('participants'))
    for b in shown:
        b.clash = any(o is not b and o.start < b.end and b.start < o.end for o in shown)
    moves(shown)
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
        'agenda': shown, 'now': timezone.now(),
        'letters': person.messages.filter(read=None)[:3],
    })


def inbox(request):
    return render(request, 'core/inbox.html', {'letters': request.user.messages.all()})


def account(request):
    """Mein Konto: address and phone number, kept up by the person; name and birth data only the office changes, on proof."""
    form = ContactForm(request.POST or None, instance=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, 'Kontaktdaten gespeichert.')
        return redirect('account')
    staff = not request.user.roles & {'student', 'applicant', 'alumni'}  # employees: their personnel file is HR's
    return render(request, 'core/account.html', {
        'form': form, 'staff': staff, 'office': 'die Personalabteilung' if staff else Office.holding('manage_students'), 'consents': request.user.consents.all(),
    })


@require_POST
def delivery(request):
    """Bescheide in the Postfach or by post: the consent of § 41 Abs. 2a VwVfG, given or taken back for what comes after."""
    person = request.user
    person.electronic = None if person.electronic else timezone.now()
    person.save(update_fields=['electronic'])
    messages.success(request, f'Bescheide kommen ab jetzt {"elektronisch ins Postfach" if person.electronic else "per Post"}.')
    return redirect('account')


@require_POST
def revoke(request, pk):
    """Widerruf (Art. 7 Abs. 3 DSGVO): the documents given under the consent are deleted; what was decided before stays."""
    consent = get_object_or_404(Consent, pk=pk, person=request.user, revoked=None)
    with because('Einwilligung widerrufen'):
        for d in consent.documents.all():
            d.drop_file()
            d.delete()
    consent.revoked = timezone.now()
    consent.save(update_fields=['revoked'])
    messages.success(request, f'Einwilligung widerrufen, {consent} gelöscht. Was bis dahin entschieden ist, bleibt bestehen.')
    return redirect('account')


ROLES = {'student': 'Studierende', 'applicant': 'Bewerbende', 'teacher': 'Lehrende', 'alumni': 'Ehemalige'}


def demo_people():
    """The demo's people in the configured order, each with what they stand for: a label of the demo's, their office, a
    doctorate, else their role."""
    found = Person.objects.prefetch_related('groups').in_bulk(settings.DEMO, field_name='username')
    shown = []
    for username in settings.DEMO:
        if person := found.get(username):
            offices = person.groups.all()
            label = settings.DEMO_LABELS.get(username) or (offices[0].name if offices else '')
            if not label and person.studies.filter(status=Study.Status.ENROLLED, program__kind='5').exists():
                label = 'Promovierende'
            shown.append((label or next((label for role, label in ROLES.items() if role in person.roles), ''), person))
    return shown


DEMO_DAYS = 30  # how long a browser keeps the access code
GUESSES = 10  # wrong codes from one address before it waits a quarter of an hour


def ticket():
    """What a browser keeps once it has given the demo's access code; tied to the code, so a new code shuts everyone out."""
    return salted_hmac('demo', settings.DEMO_CODE.lower()).hexdigest()


def admitted(request):
    return bool(settings.DEMO_CODE) and constant_time_compare(request.COOKIES.get('demo', ''), ticket())


@login_not_required
def gate(request):
    """Only in the demo: the shared access code, given once per browser; an address that keeps guessing waits."""
    if not settings.DEMO_CODE:
        raise Http404
    target = request.GET.get('next', '')
    target = target if url_has_allowed_host_and_scheme(target, {request.get_host()}) else reverse('login')
    if request.method == 'POST':
        key = 'gate:' + request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[-1].strip()  # as the proxy saw it
        if (tries := cache.get(key, 0)) >= GUESSES:
            messages.error(request, 'Zu viele Versuche. Bitte in einer Viertelstunde noch einmal.')
        elif constant_time_compare(request.POST.get('code', '').strip().lower(), settings.DEMO_CODE.lower()):
            response = redirect(target)
            response.set_cookie('demo', ticket(), max_age=DEMO_DAYS * 86400, secure=not settings.DEBUG, httponly=True, samesite='Lax')
            return response
        else:
            cache.set(key, tries + 1, 15 * 60)
            messages.error(request, 'Dieser Zugangscode stimmt nicht.')
    return render(request, 'core/gate.html')


def stale(request, reason=''):
    """A form sent from a page gone stale, typically one from before signing in or out: offer a fresh copy of it."""
    back = request.META.get('HTTP_REFERER', '')
    return render(request, 'core/stale.html', {'back': back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else reverse('home')}, status=403)


@login_not_required
@require_POST
def demo(request):
    """Only in the demo, past its access code: sign in as one of its people, without a password."""
    if request.POST.get('konto') not in settings.DEMO or not admitted(request):
        raise Http404
    login(request, get_object_or_404(Person, username=request.POST['konto']), backend=settings.AUTHENTICATION_BACKENDS[0])
    return redirect('home')


def message(request, pk):
    letter = get_object_or_404(Message, pk=pk, person=request.user)
    fresh = not letter.read
    if fresh:
        letter.read = timezone.now()
        letter.save(update_fields=['read'])
    place = letter.link and dict((app.label, title) for app, title in tiles(request.user)).get(resolve(urlsplit(letter.link).path).app_name)
    response = render(request, 'core/message.html', {'letter': letter, 'place': place or 'Campus'})
    if fresh:  # read in the side panel: the Postfach underneath, its counter with it, reloads once the panel closes
        response['X-Stale'] = '1'
    return response


def letter(request, pk):
    """A message from the Postfach as a printable letter, for its recipient; a Bescheid in the student file also for the
    student office and the office deciding results (docs/privacy.md)."""
    letter = get_object_or_404(Message.objects.select_related('person'), pk=pk)
    filed = Document.objects.filter(message=letter).first()
    if not (letter.person == request.user or filed and any(request.user.may(c, filed) for c in ('manage_students', 'decide_results'))):
        raise PermissionDenied
    return render(request, 'core/letter.html', {'letters': [letter]})


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


def history(request, table, row):
    """A record's history, for the panel; a person's is everything about them. Each office sees the changes to the
    records it looks after, and may undo them."""
    try:
        model = apps.get_model(table)
    except (LookupError, ValueError):
        raise Http404
    if not issubclass(model, Tracked):
        raise Http404
    record = model.objects.filter(pk=row).first()
    if not (seen := model.seen_by(request.user, row)):
        raise PermissionDenied(f'Diesen Verlauf {keepers(model, record)}.')
    shown = tables(seen)
    changes = Change.objects.filter(model.history(row), table__in=shown).select_related('by')[:200]
    return render(request, 'core/history.html', {
        'record': record, 'changes': describe(changes, request.user), 'others': Change.objects.filter(model.history(row)).exclude(table__in=shown).exists(),
    })


def keepers(model, record):
    """The end of a sentence on who may see or change a record: what it is and the offices that look after it; never
    its own name, which the refused may not know."""
    label = record.get_kind_display() if isinstance(record, Course) else model._meta.verbose_name
    offices = Office.holding(model.keepers(record))
    return f'({label}) sehen nur die dafür zuständigen Ämter{": " + offices if offices else ""}'


@require_POST
def undo(request, pk):
    change = get_object_or_404(Change, pk=pk)
    model = apps.get_model(change.table)
    record = model.objects.filter(pk=change.row).first()
    if not model.held(request.user, record or former(change), deleted=record is None, undo=True):
        offices = Office.holding(model.undoers(record))
        raise PermissionDenied(f'Diese Änderung setzen nur die dafür zuständigen Ämter zurück{": " + offices if offices else ""}.')
    try:
        restore(change)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Wiederhergestellt.' if change.action == Change.Action.DELETED else 'Zurückgesetzt.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else 'home')


def denied(request, exception):
    """Kein Zugriff: why, where the code says it, whom to ask for a right, and the way back."""
    back = request.headers.get('Referer', '')
    return render(request, '403.html', {
        'reason': str(exception), 'accounts': Office.holding('manage_accounts'),
        'back': back if back != request.build_absolute_uri() and url_has_allowed_host_and_scheme(back, {request.get_host()}) else '',
    }, status=403)


def when(request):
    """What a date field understood, for the hint next to it."""
    text = request.GET.get('q', '')
    if request.GET.get('nur') == 'vergangen':  # a day that has been: why it is refused
        try:
            day = dates.past(text)
        except ValidationError as error:
            return JsonResponse({'error': error.messages[0]})
        return JsonResponse({'iso': day.isoformat(), 'text': date_format(day, 'l, j. F Y')})
    found = (dates.day(text), None) if request.GET.get('nur') == 'tag' else dates.moment(text)
    if not found or not found[0]:
        return JsonResponse({})
    day, time = found
    return JsonResponse({'iso': day.isoformat(), 'text': date_format(day, 'l, j. F Y') + (f', {time:%H:%M} Uhr' if time else '')})


def towns(request):
    """Gemeinden by name or postcode, for a place field (app.js placed): each with its Kreis today. One no longer on its
    own is found too, by the name it had, as where an HZB was gained."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    found = Key.objects.filter(listing=Key.List.GEMEINDE)
    hits = sorted(found.filter(postcode__startswith=q)[:12] if q.isdigit() else ranked(found, q, 'name', limit=12), key=lambda k: k.until is not None)
    moved, kreise = replaced(DISTRICTS), dict(Key.objects.filter(listing=Key.List.KREIS).values_list('code', 'name'))
    return JsonResponse([
        {'text': ' · '.join(filter(None, [k.name + (f' (bis {k.until.year})' if k.until else ''), k.postcode, kreise.get(moved.get(k.within) or k.within)])),
         'code': k.code, 'kreis': moved.get(k.within) or k.within}
        for k in hits
    ], safe=False)


def search(request):
    """One box for everything: places in the apps, dates, and every record the person may see."""
    q = request.GET.get('q', '').strip()
    fragment = request.headers.get('X-Fragment')  # the list that opens under the search box
    groups = [(label, hits) for label, hits in found(request.user, q, 6 if fragment else 30) if hits]
    return render(request, 'core/_found.html' if fragment else 'core/search.html', {'q': q, 'groups': groups, 'fragment': fragment})


def hit(title, url, meta=''):
    return {'title': title, 'url': url, 'meta': meta}


STANDING = [  # whose study a search for a name shows first
    Study.Status.ENROLLED, Study.Status.GUEST, Study.Status.ADMITTED, Study.Status.APPLIED, Study.Status.WAITLISTED,
    Study.Status.GRADUATED, Study.Status.LEFT, Study.Status.REJECTED,
]


def people(studies, limit, person):
    """One hit per person, at their most current study: the searcher's own first (their class, their courses, their
    Institut's), enrolled before turned-down applications. The birth date tells namesakes apart, for the offices that
    may see it (docs/privacy.md)."""
    shown, dated = {}, bool(person.roles & {'manage_students', 'decide_results', 'view_applications'})
    taught = Participation.objects.filter(study=OuterRef('pk'), course__staff=person)
    own = Case(When(Q(teacher=person) | Q(Exists(taught)) | Q(subject__institute__in=person.ours('view_files')), then=0), default=1)
    rank = Case(*(When(status=status, then=n) for n, status in enumerate(STANDING)))
    for s in studies.annotate(own=own, rank=rank).order_by('own', 'rank', 'person__last_name', 'person__first_name')[:limit * 10]:
        if s.person_id in shown:
            shown[s.person_id][1] += 1
        elif len(shown) < limit:
            shown[s.person_id] = [s, 0]
    return [
        hit(s.person.name, s.get_absolute_url(), ' · '.join(filter(None, [
            dated and s.person.birth_date and f'geb. {date_format(s.person.birth_date, "j.n.Y")}', s.person.matriculation, f'{s.subject} {s.program.degree}',
            s.state, more and plural(more, 'weiteres Studium', 'weitere Studien'),
        ])))
        for s, more in shown.values()
    ]


def ranked(queryset, q, *fields, limit, names=''):
    """Matches where a word begins first, then those inside a word, as German compounds need ('plan' in
    'Studienverlaufsplan'); at most `limit`."""
    first = list(matching(queryset, q, *fields, names=names, starts=True)[:limit])
    rest = matching(queryset, q, *fields, names=names).exclude(pk__in=[o.pk for o in first])[:limit - len(first)] if len(first) < limit else []
    return first + list(rest)


def found(person, q, limit):
    roles, sought = person.roles, words(q)
    groups = []
    if q and roles - {'applicant', 'alumni'} and (when := dates.moment(q)):
        groups.append(('Springen zu', jumps(roles, *when)))
    # every app's places, also of apps without a tile for this person: a student applying for a Master
    places = [(f, app.title(roles) or app.verbose_name) for app in tile_apps() for f in app.features(person)]
    places += [(feature('Startseite', reverse('home'), 'aufgaben zu erledigen übersicht'), ''), (feature('Postfach', reverse('inbox'), 'nachrichten bescheide mitteilungen'), ''),
               (feature('Mein Konto', reverse('account'), 'anschrift adresse telefon kontaktdaten umzug'), '')]
    if person.is_superuser:
        places.append((feature('Datenpflege', reverse('admin:index'), 'admin stammdaten verwaltung'), ''))
    groups.append(('Funktionen', [
        hit(f['title'], f['url'], app) for f, app in places if all(w in fold(f"{f['title']} {f['words']} {app}") for w in sought)
    ][:limit if q else None]))  # an empty box lists everything the person can do
    if len(q) < 2:
        return groups
    if roles & {'view_files', 'manage_students', 'view_applications', 'teacher'}:  # only the files and applications the person may open
        seen = Q(teacher=person) & ~Q(status__in=Study.APPLICATION) if 'teacher' in roles else Q(pk=None)
        seen |= Q(pk__in=person.within(Study.objects.exclude(status__in=Study.APPLICATION), 'view_files', 'manage_students').values('pk'))
        lapsed = Q(status=Study.Status.REJECTED, since__lt=months_before(timezone.localdate(), Institution.get().retention_months))  # an old rejection is nobody's business
        seen |= Q(pk__in=person.within(Study.objects.filter(status__in=Study.APPLICATION).exclude(lapsed), 'view_applications').values('pk'))
        rating = Study.objects.filter(status=Study.Status.APPLIED, participations__course__kind=Course.Kind.AUDITION, participations__course__staff=person)
        seen |= Q(pk__in=rating.values('pk'))  # applications one rates, to their page for the jury
        studies = matching(Study.objects.filter(seen).select_related('person', 'program', 'subject'), q, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__matriculation', 'person__email', starts=True)
        groups.append(('Studierende und Bewerbungen', people(studies, limit, person)))
    if 'teacher' in roles:  # whom one teaches outside one's class, by the course: their file is not one's own
        taught = (
            Participation.objects.filter(course__term=Term.current(), study__status=Study.Status.ENROLLED)
            .filter(Q(course__staff=person) | Q(study__ensembles__coach=person, study__ensembles__course=F('course'))).exclude(study__teacher=person)
        )
        first = {}
        for p in matching(taught.select_related('study__person', 'course'), q, 'study__person__first_name', 'study__person__last_name', starts=True)[:limit * 5]:
            first.setdefault(p.study.person_id, p)
        groups.append(('In Ihrem Unterricht', [hit(p.study.person.name, p.course.get_absolute_url(), p.course.title) for p in list(first.values())[:limit]]))
    if roles - {'applicant', 'alumni'}:  # whom to ask: who teaches or holds an office, and the offices
        staff = Person.objects.filter(Q(teaching__isnull=False) | Q(groups__isnull=False), is_active=True).distinct().select_related('institute')
        groups.append(('Personen', [
            hit(f'{p.title} {p.first_name} {p.last_name}'.strip(), f'mailto:{p.email}', ' · '.join(filter(None, [p.institute and str(p.institute), p.email])))
            for p in ranked(staff, q, 'first_name', 'last_name', limit=limit)
        ]))
        offices = []
        for o in ranked(Office.objects.prefetch_related('user_set', 'institutes', 'programs'), q, 'name', limit=limit, names='amt'):
            members = [p for p in o.user_set.all() if p.is_active]
            mail = o.email or ','.join(p.email for p in members)
            offices.append(hit(o.name + limited(o), f'mailto:{mail}', ' · '.join(filter(None, [o.email, ', '.join(f'{p.first_name} {p.last_name}' for p in members)]))))
        groups.append(('Ämter', offices))
    teaching = [Course.Kind.COURSE, Course.Kind.LESSON]
    courses = matching(Course.objects.filter(term__end__gte=timezone.localdate(), term__in=Term.shown(person)).select_related('term'), q, 'title', 'staff__last_name', 'subject__name', starts=True)
    own = Exists(Module.objects.filter(courses=OuterRef('pk'), program__studies__person=person, program__studies__status=Study.Status.ENROLLED))  # one's own Studiengang's first
    groups.append(('Lehrveranstaltungen', [
        hit(c.title, c.get_absolute_url(), f'{c.get_kind_display()} · {c.term}') for c in courses.filter(kind__in=teaching).annotate(own=own).order_by('-own', 'title')[:limit]
    ]))
    papers = Q(staff=person)  # exams and auditions: those the person examines, those within reach for the offices running them
    written = Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.RECOGNITION, Course.Kind.THESIS])
    papers |= Q(pk__in=person.within(written, 'manage_exams', 'decide_results', 'recognise').values('pk'))
    papers |= Q(pk__in=person.within(Course.objects.filter(kind=Course.Kind.AUDITION), 'run_auditions').values('pk'))
    exams = courses.exclude(kind__in=teaching).filter(papers).distinct()
    groups.append(('Prüfungen', [hit(c.title, c.get_absolute_url(), f'{c.get_kind_display()} · {c.term}') for c in exams[:limit]]))
    groups.append(('Studiengänge', [hit(str(p), p.get_absolute_url()) for p in ranked(Program.objects.all(), q, 'name', 'degree', limit=limit, names='studiengang studienplan')]))
    groups.append(('Module', [
        hit(f'{m.code} {m.title}', m.program.get_absolute_url(), str(m.program))
        for m in ranked(Module.objects.select_related('program'), q, 'code', 'title', limit=limit, names='modul')
    ]))
    if roles - {'applicant', 'alumni'}:
        groups.append(('Räume', [
            hit(r.name, reverse('rooms:room', args=[r.pk]), ' · '.join(filter(None, [r.purpose, r.building, f'{r.capacity} Plätze'])))
            for r in ranked(Room.objects.all(), q, 'name', 'purpose', 'building', 'equipment', limit=limit, names='raum')
        ]))
        # bookings by title or category: concerts for everyone, one's own dates, everything for the offices
        shown = Booking.objects.filter(end__gte=timezone.now()).select_related('room', 'course', 'category')
        if not person.everywhere('view_bookings'):
            shown = shown.filter(Q(category__public=True) | Q(pk__in=agenda(person).values('pk')) | Q(room__in=person.reached(Room, 'view_bookings')))
        named = [c.key for c in BookingCategory.objects.all() if all(w in fold(c.name) for w in sought)]
        events = shown.filter(Q(pk__in=matching(shown, q, 'title', 'description', 'course__title').values('pk')) | Q(category__in=named))
        nearest = {}  # a course's or series' next date only, not every lesson of the term
        for b in events.order_by('start')[:limit * 20]:
            nearest.setdefault(b.course_id or b.series or b.pk, b)
        groups.append(('Termine', [
            hit(b.label, reverse('rooms:booking', args=[b.pk]), f'{date_format(timezone.localtime(b.start), "D j.n., H:i")} · {b.room} · {b.category}')
            for b in list(nearest.values())[:limit]
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
