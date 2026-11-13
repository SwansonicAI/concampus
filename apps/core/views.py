from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import login
from django.core import signing
from django.contrib.auth.decorators import login_not_required
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, Exists, F, Min, OuterRef, Prefetch, Q, When, Window
from django.db.models.functions import RowNumber
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from courses.models import Course, Participation
from curriculum.models import Module, Program, Subject
from rooms.models import Booking, BookingCategory, Room
from rooms.plans import agenda, moves
from students.models import Document, Study
from . import dates, texts
from . import access
from .access import CODENAMES, areas
from .apps import feature, plural, tile_apps, tiles
from .backends import address
from .days import busy, strip
from .dossier import dossier
from .forms import AskForm, ContactForm, EmailForm, FeedbackForm
from .greetings import greeting
from .history import because, describe, former, restore, tables
from .dates import months_before
from .keys import DISTRICTS, replaced
from .models import AccessRequest, Change, Consent, Export, Institution, Key, Message, Office, Person, Term, Tracked, limited
from .index import find
from .text import ACTS, fit, fold, matching, meant, stem, words


def home(request):
    person, today = request.user, timezone.localdate()
    monday = today - timedelta(days=today.weekday())  # four whole weeks, this one first; a day opens in the side panel (day)
    marked = busy(agenda(person), monday, 28)
    titles = dict(tiles(person))
    tasks = sorted(  # from every app, also one without a tile for the person, such as their own loan or stay abroad
        ({**t, 'app': titles.get(app, app.verbose_name)} for app in tile_apps() for t in app.tasks(person)),
        key=lambda t: (t['due'] is None, t['due'] or today),
    )
    return render(request, 'core/home.html', {
        'greeting': greeting(person, timezone.localtime()),
        'term': Term.current(),
        'today': today,
        'soon': today + timedelta(days=7),
        'tasks': tasks,
        'calendar': bool(marked),
        'days': strip(monday, today, marked, 28),
        'letters': person.messages.filter(read=None)[:3],
    })


def day(request):
    """One's appointments on a day, with clashes and the moves between buildings."""
    person, today = request.user, timezone.localdate()
    day = dates.day(request.GET.get('tag', '')) or today
    shown = list(agenda(person).filter(**dates.starting(day)).select_related('room', 'course', 'category').prefetch_related('participants'))
    for b in shown:
        b.clash = any(o is not b and o.start < b.end and b.start < o.end for o in shown)
    moves(shown)
    return render(request, 'core/day.html', {'day': day, 'today': today, 'agenda': shown, 'now': timezone.now()})


def inbox(request):
    return render(request, 'core/inbox.html', {'letters': request.user.messages.all()})


def account(request):
    """Mein Konto: address and phone number, kept up by the person; name and birth data only the office changes, on proof."""
    person = request.user
    changing = person.roles == {'applicant'}  # an applicant's address is their own; a student's is the Hochschule's
    posted = request.POST if request.method == 'POST' else None
    email = EmailForm(posted if 'neue_adresse' in request.POST else None, person=person) if changing else None
    form = ContactForm(None if email and email.is_bound else posted, instance=person)
    if email and email.is_valid():
        address, token = email.cleaned_data['email'], signing.dumps({'person': person.pk, 'email': email.cleaned_data['email']}, salt='email')
        subject, body = texts.render('email_bestaetigen', vorname=person.first_name, nachname=person.last_name, link=request.build_absolute_uri(reverse('confirm_email', args=[token])))
        send_mail(subject, body, Institution.get().email or None, [address], fail_silently=True)
        messages.success(request, f'Bestätigungslink an {address} gesendet; bis Sie ihn öffnen, gilt die bisherige Adresse.')
        return redirect('account')
    if form.is_valid():
        form.save()
        messages.success(request, 'Kontaktdaten gespeichert.')
        return redirect('account')
    staff = not person.roles & {'student', 'applicant', 'alumni'}  # employees: their personnel file is HR's
    offices = person.offices_held()  # what one may do, to ask for more where it falls short
    return render(request, 'core/account.html', {
        'form': form, 'email_form': email, 'staff': staff, 'office': 'die Personalverwaltung' if staff else Office.holding('manage_students', linked=True),
        'consents': person.consents.all(), 'sign_ins': person.sign_ins.all()[:5],
        'offices': offices, 'areas': offices and areas(person.marks), 'accounts': offices and Office.holding('manage_accounts', linked=True),
    })


@login_not_required
@transaction.atomic  # a GET, but a change with its history
def confirm_email(request, token):
    """A new sign-in address, opened from the link sent to it: within three days, and still nobody else's."""
    try:
        data = signing.loads(token, salt='email', max_age=3 * 86400)
    except signing.BadSignature:
        messages.error(request, 'Der Link ist abgelaufen oder ungültig. Bitte die Änderung unter „Mein Konto“ neu anstoßen.')
        return redirect('account' if request.user.is_authenticated else 'login')
    person = get_object_or_404(Person, pk=data['person'], is_active=True)
    if Person.objects.filter(email__iexact=data['email']).exclude(pk=person.pk).exists():
        messages.error(request, 'Diese Adresse gehört inzwischen zu einem anderen Konto.')
    else:
        person.email = data['email']
        person.save(update_fields=['email'])
        messages.success(request, f'Ihre E-Mail-Adresse ist jetzt {person.email}.')
    return redirect('account' if request.user.is_authenticated else 'login')


def copy(request):
    """A copy of what ConCampus keeps about the person (Art. 15 Abs. 3 DSGVO)."""
    Export.log(request.user, 'Eigene Daten (Art. 15 DSGVO)', 1)
    return FileResponse(dossier(request.user), as_attachment=True, filename=f'campus-{request.user.username}.zip')


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
    doctorate, else their role; [(label, [(person, detail)])], the demo's student and teacher with others to choose from."""
    found = Person.objects.prefetch_related('groups').in_bulk(settings.DEMO, field_name='username')
    shown = []
    for username in settings.DEMO:
        if person := found.get(username):
            offices = person.groups.all()
            label = settings.DEMO_LABELS.get(username) or (offices[0].name if offices else '')
            if not label and person.studies.filter(status=Study.Status.ENROLLED, program__kind='5').exists():
                label = 'Promovierende'
            role = '' if label else next((role for role in ROLES if role in person.roles), '')
            shown.append((label or ROLES.get(role, ''), DEMO_MORE[role](person) if role in DEMO_MORE else [(person, '')]))
    return shown


def demo_students(first):
    """The demo's student, then the first enrolled in each other programme and subject, with what they study; each
    person and each programme and subject once."""
    enrolled = Study.objects.filter(status=Study.Status.ENROLLED).exclude(program__kind='5')  # doctoral candidates: 'promotion'
    firsts = enrolled.exclude(person__username__in=settings.DEMO).values('program', 'subject').annotate(first=Min('pk')).values('first')
    studies = enrolled.filter(Q(person=first) | Q(pk__in=firsts)).select_related('person', 'program', 'subject')
    chosen = {}
    for s in sorted(studies, key=lambda s: (s.person != first, s.program.degree, s.subject.name)):
        detail = f'{s.program.degree} {s.subject}'
        if s.person not in chosen and detail not in chosen.values():
            chosen[s.person] = detail
    return list(chosen.items())


def demo_teachers(first):
    """The demo's teacher, then the first three who teach in each Institut, with the Institut."""
    teachers = Person.objects.filter(teaching__isnull=False, institute__isnull=False).exclude(username__in=settings.DEMO).values('pk')
    firsts = (
        Person.objects.filter(pk__in=teachers).annotate(n=Window(RowNumber(), partition_by='institute', order_by='pk'))
        .filter(n__lte=3).values('pk')
    )
    people = Person.objects.filter(Q(pk=first.pk) | Q(pk__in=firsts)).select_related('institute')
    return [(p, p.institute) for p in sorted(people, key=lambda p: (p != first, str(p.institute)))]


DEMO_MORE = {'student': demo_students, 'teacher': demo_teachers}


DEMO_DAYS = 30  # how long a browser keeps the access code
GUESSES = 10  # wrong codes from one address before it waits a quarter of an hour


def ticket():
    """What a browser keeps once it has given the demo's access code; tied to the code, so a new code shuts everyone out."""
    return salted_hmac('demo', settings.DEMO_CODE.lower()).hexdigest()


def admitted(request):
    return bool(settings.DEMO_CODE) and constant_time_compare(request.COOKIES.get('demo', ''), ticket())


def logo(request):
    """The Hochschule's logo, for the letterhead; an SVG cannot run scripts from here."""
    if not (logo := Institution.get().logo):
        raise Http404
    response = FileResponse(logo.open('rb'), content_type='image/svg+xml' if logo.name.endswith('.svg') else 'image/png')
    response['Content-Security-Policy'] = 'sandbox'
    return response


@login_not_required
def accessibility(request):
    """Erklärung zur Barrierefreiheit (the Land's law on accessible information technology), with the form to report a
    barrier; signed in, name and address come filled in."""
    person = request.user if request.user.is_authenticated else None
    form = FeedbackForm(request.POST or None, initial={'page': request.GET.get('seite', '')} | ({'name': str(person), 'email': person.email} if person else {}))
    if form.is_valid():
        feedback = form.save()
        if to := Institution.get().email:
            transaction.on_commit(lambda: send_mail('Barriere in ConCampus gemeldet', f'{feedback.text}\n\n{feedback.page}\n{feedback.name} <{feedback.email}>', None, [to], fail_silently=True))
        messages.success(request, f'Danke. Wir antworten bis zum {date_format(feedback.due, "j. F Y")}.')
        return redirect('accessibility')
    return render(request, 'core/accessibility.html', {'form': form})


@login_not_required
def gate(request):
    """Only in the demo: the shared access code, given once per browser; an address that keeps guessing waits."""
    if not settings.DEMO_CODE:
        raise Http404
    target = request.GET.get('next', '')
    target = target if url_has_allowed_host_and_scheme(target, {request.get_host()}) else reverse('login')
    if request.method == 'POST':
        key = f'gate:{address(request)}'
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
    if not admitted(request) or request.POST.get('konto') not in {p.username for _, people in demo_people() for p, _ in people}:
        raise Http404
    login(request, get_object_or_404(Person, username=request.POST['konto']), backend=settings.AUTHENTICATION_BACKENDS[0])
    return redirect('home')


@login_not_required
def demo_moodle(request):
    """Only in the demo, past its access code: where the demo's Moodle sends whoever signs in (docs/moodle.md); one of its
    people chosen opens Moodle as them, ConCampus's own sign-in left as it is."""
    from courses.moodle import entry  # which imports this module

    if not settings.MOODLE['url'] or not admitted(request):
        raise Http404
    if request.method == 'POST':
        if request.POST.get('konto') not in {p.username for _, people in demo_people() for p, _ in people}:
            raise Http404
        return redirect(entry(request.POST['konto']))
    return render(request, 'core/demo_moodle.html', {'demo': demo_people})


def message(request, pk):
    letter = get_object_or_404(Message, pk=pk, person=request.user)
    fresh = not letter.read
    if fresh:
        letter.read = timezone.now()
        letter.save(update_fields=['read'])
    place = letter.link and dict((app.label, title) for app, title in tiles(request.user)).get(resolve(urlsplit(letter.link).path).app_name)
    response = render(request, 'core/message.html', {'letter': letter, 'place': place or 'ConCampus'})
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
    back = request.GET.get('next', '')
    back = back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else getattr(record, 'get_absolute_url', lambda: '')()
    return render(request, 'core/history.html', {
        'record': record, 'changes': describe(changes, request.user), 'others': Change.objects.filter(model.history(row)).exclude(table__in=shown).exists(),
        'trail': [(None, back), (str(record), back)] if record and back else [],
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
    if not model.powers(request.user, record or former(change), deleted=record is None, undo=True):
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
    """Kein Zugriff: why, where the code says it, or whom the page is for and which offices hold the rights it needs;
    whom to ask for a right, and the way back."""
    back = request.headers.get('Referer', '')
    back = back if back != request.build_absolute_uri() and url_has_allowed_host_and_scheme(back, {request.get_host()}) else ''
    roles, holding = getattr(exception, 'roles', ()), {}
    for o in Office.objects.filter(permissions__codename__in=roles).distinct().prefetch_related('permissions', 'institutes', 'programs'):
        for c in o.marks.keys() & set(roles):
            holding.setdefault(c, []).append(o)
    needed, staff = [c for c in roles if c in CODENAMES], request.user.is_authenticated and not request.user.roles & ASKING
    return render(request, '403.html', {
        'trail': [(None, back or reverse('home'))],  # the menu stays where one came from, not on the page refused
        'reason': str(exception), 'roles': roles, 'who': [access.ROLES[r] for r in roles if r in access.ROLES], 'several': len(needed) > 1,
        'areas': areas({c: holding.get(c, []) for c in needed}, every=False), 'inside': bool(getattr(request.user, 'roles', set()) - OUTSIDE),
        'accounts': Office.holding('manage_accounts', linked=True), 'back': back, 'staff': staff,
        'asked': {a.capability: a for a in request.user.access_requests.all()} if staff else {},
    }, status=403)


ASKING = {'student', 'applicant', 'alumni'}  # who asks for no Berechtigung: those who study, or did, or want to


@require_POST
def ask(request):
    """A Berechtigung asked for from Kein Zugriff, by whoever works here; asked again, what for and until when change."""
    c, form = request.POST.get('berechtigung', ''), AskForm(request.POST)
    if c not in CODENAMES or request.user.roles & ASKING or c in request.user.roles:
        raise PermissionDenied
    if form.is_valid():
        AccessRequest.objects.update_or_create(person=request.user, capability=c, defaults=form.cleaned_data)
        messages.success(request, f'„{access.SHORT[c]}“ beantragt. Die Entscheidung kommt ins Postfach.')
    else:
        messages.error(request, 'Nicht beantragt: bitte angeben, wofür, und das Datum etwa als 31.3.2027.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else 'home')


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


OUTSIDE = {'applicant', 'alumni'}  # who finds the offices by their shared address only, not who works here (docs/privacy.md)


def staff():
    """Who teaches or holds an office, a stand-in too: whom everyone inside may find, to know whom to ask."""
    standing = Q(memberships__confirmed_by__isnull=False, memberships__until__gte=timezone.localdate())
    return Person.objects.filter(Q(teaching__isnull=False) | Q(groups__isnull=False) | standing, is_active=True).distinct()


def directory(request):
    """Personen und Ämter: who is responsible for what and how to reach them, as the Hochschule's website lists its
    people; for applicants and alumni the offices with a shared address only."""
    inside, q = bool(request.user.roles - OUTSIDE), request.GET.get('q', '').strip()
    offices = [o for o in (Office.staffed() if inside else Office.staffed().exclude(email='')) if all(w in fold(f'{o.name} {o.remit}') for w in words(q))]
    people, letters, letter = [], [], ''
    if inside:  # by the first letter of the last name, A–Z, or all that fit what is searched
        held = {}
        for o in Office.staffed():
            for p in [*o.user_set.all(), *(m.person for m in o.standing_in)]:
                held.setdefault(p.pk, []).append(o)
        found = list(find(staff().select_related('institute'), q, 'first_name', 'last_name', 'institute__name', starts=True).order_by('last_name', 'first_name'))
        letters = sorted({fold(p.last_name[:1]).upper() for p in found})
        letter = '' if q else request.GET.get('buchstabe', letters[0] if letters else '')
        people = [(p, held.get(p.pk, [])) for p in found if not letter or fold(p.last_name[:1]).upper() == letter]
    return render(request, 'core/directory.html', {
        'q': q, 'inside': inside, 'offices': offices, 'people': people, 'counted': len(found) if inside else 0, 'letters': letters, 'letter': letter,
        'tab': 'personen' if inside and request.GET.get('zeige') == 'personen' else 'aemter',
    })


def colleague(request, pk):
    """A colleague's page: who they are, the offices they hold and how far, what they teach, and their work e-mail;
    nothing else of them (docs/privacy.md, Staff). What an office may do, only to whoever gives rights, or one's own."""
    if not request.user.roles - OUTSIDE:
        raise PermissionDenied
    person, term = get_object_or_404(staff().select_related('institute'), pk=pk), Term.current()
    courses = person.teaching.filter(term=term, kind__in=[Course.Kind.COURSE, Course.Kind.LESSON], cancelled=None).order_by('title') if term else []
    rights = 'manage_accounts' in request.user.roles or person == request.user
    return render(request, 'core/colleague.html', {
        'trail': [(None, reverse('directory'))], 'person': person, 'offices': person.offices_held(), 'courses': courses, 'term': term,
        'areas': rights and areas(person.marks),
    })


def office(request, pk):
    """An office's page: how far it reaches, how to reach it, and for everyone inside who is in it; what it may do, only
    to whoever gives rights."""
    inside = bool(request.user.roles - OUTSIDE)
    office = get_object_or_404(Office.staffed() if inside else Office.staffed().exclude(email=''), pk=pk)
    return render(request, 'core/office.html', {
        'trail': [(None, reverse('directory'))], 'inside': inside, 'office': office, 'areas': 'manage_accounts' in request.user.roles and areas(office.marks),
    })


def search(request):
    """One box for everything: places in the apps, dates, and every record the person may see."""
    q = request.GET.get('q', '').strip()
    fragment = request.headers.get('X-Fragment')  # the palette's list
    quick = 'schnell' in request.GET  # the palette's first answer, at once: the records follow
    groups = [(label, hits) for label, hits in found(request.user, q, 6 if fragment else 30, quick) if hits]
    if quick and not groups:  # not „Nichts gefunden“ before the records are in
        return HttpResponse()
    for label, hits in groups:
        for h in hits:
            h['icon'] = h['icon'] or getattr(apps.app_configs.get(KINDS[label]), 'icon', KINDS[label])
    return render(request, 'core/_found.html' if fragment else 'core/search.html', {'q': q, 'groups': groups, 'fragment': fragment})


def hit(title, url, meta='', icon=''):
    return {'title': title, 'url': url, 'meta': meta, 'icon': icon}


# What a hit is, at a glance as in Listary, with the apps' icons (Tile.icon) and these: SVG path data, 24×24, stroked
PAGE = 'M14 3H6v18h12V7zM14 3v4h4'
PERSON = 'M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z'
OFFICE = 'M3 21h18M5 21V8l7-5 7 5v13M10 21v-6h4v6'
DOOR = 'M3 21h18M6 21V3h12v18M14 12v.5'
MAIL = 'M3 5h18v14H3zM3 6l9 7 9-7'
KINDS = {  # each group's, by an app's label or as drawn
    'Springen zu': 'rooms', 'Studierende und Bewerbungen': 'students', 'In Ihrem Unterricht': 'students', 'Personen': PERSON, 'Ämter': OFFICE,
    'Lehrveranstaltungen': 'courses', 'Prüfungen': 'exams', 'Studiengänge': 'curriculum', 'Module': 'curriculum', 'Räume': DOOR, 'Termine': 'rooms', 'Postfach': MAIL,
}
FIRST = {  # the group a search names first, by any word meaning the same: 'piano exam', 'violin teacher' (synonyms.KINDS)
    'lehrende': 'Personen', 'studierende': 'Studierende und Bewerbungen', 'bewerbung': 'Studierende und Bewerbungen', 'teilnehmende': 'In Ihrem Unterricht',
    'lehrveranstaltung': 'Lehrveranstaltungen', 'unterricht': 'Lehrveranstaltungen', 'prüfung': 'Prüfungen', 'raum': 'Räume', 'modul': 'Module',
    'studiengang': 'Studiengänge', 'amt': 'Ämter', 'termin': 'Termine',
}


STANDING = [  # whose study a search for a name shows first
    *Study.ATTENDING, Study.Status.ADMITTED, Study.Status.APPLIED, Study.Status.WAITLISTED,
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
    for s in studies.annotate(own=own, rank=rank).order_by('own', '_fit', 'rank', 'person__last_name', 'person__first_name')[:limit * 10]:
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
    first = list(find(queryset, q, *fields, names=names, starts=True)[:limit])
    rest = find(queryset, q, *fields, names=names).exclude(pk__in=[o.pk for o in first])[:limit - len(first)] if len(first) < limit else []
    return first + list(rest)


def found(person, q, limit, quick=False):
    """(label, hits) of everything the search finds; `quick`: the dates and places only, which need no records."""
    roles, sought = person.roles, words(q)
    groups = []
    if q and roles - OUTSIDE and (when := dates.moment(q)):
        groups.append(('Springen zu', jumps(roles, *when)))
    # one's own places first, so that they win a tie ('address': Mein Konto, not the Hochschule's); then every app's, also
    # of apps without a tile for this person: a student applying for a Master
    places = [(feature('Startseite', reverse('home'), 'aufgaben zu erledigen übersicht'), '', PAGE), (feature('Postfach', reverse('inbox'), 'nachrichten bescheide mitteilungen'), '', MAIL),
              (feature('Mein Konto', reverse('account'), 'anschrift adresse telefon kontaktdaten umzug ändern e-mail einwilligungen anmeldungen meine daten rechte ämter einstellungen'), '', PERSON),
              (feature('Passwort ändern', reverse('password_change'), 'kennwort passwort sicherheit'), '', PERSON),
              (feature('Meine Daten herunterladen', reverse('copy'), 'kopie auskunft dsgvo datenschutz'), '', PERSON),
              (feature('Personen und Ämter', reverse('directory'), 'lehrende kollegium mitarbeiter verzeichnis kontakt zuständig e-mail'), '', PERSON)]
    places += [(f, app.title(roles) or app.verbose_name, app.icon) for app in tile_apps() for f in app.features(person)]
    if mail := Institution.get().email:  # as the footer offers it
        places.append((feature('Hilfe und Hinweise', f'mailto:{mail}', 'frage kontakt'), '', MAIL))
    if person.is_superuser:
        places.append((feature('Datenpflege', reverse('admin:index'), 'admin stammdaten verwaltung'), '', PAGE))
    # the best fit first, of those that fit the most words, at least half; an empty box lists everything the person can do
    meaning = meant(q)
    scored = sorted(((fit(meaning, f['title'], f"{f['words']} {app}"), f, app, icon) for f, app, icon in places), key=lambda s: s[0], reverse=True)
    most, best = scored[0][0] if scored else (0, 0)
    fits = [(f, app, icon) for (n, _), f, app, icon in scored if not meaning or n == most > 0 and n >= 0.5][:limit if q else None]
    groups.append(functions := ('Funktionen', [hit(f['title'], f['url'], app, icon) for f, app, icon in fits]))
    weak = fits and best < 3 * len(meaning)  # bits of words only, as a name holds them: after the records
    if len(q) < 2 or quick:
        return groups
    if roles & {'view_files', 'manage_students', 'view_applications', 'teacher'}:  # only the files and applications the person may open
        seen = Q(teacher=person) & ~Q(status__in=Study.APPLICATION) if 'teacher' in roles else Q(pk=None)
        seen |= Q(pk__in=person.within(Study.objects.exclude(status__in=Study.APPLICATION), 'view_files', 'manage_students').values('pk'))
        lapsed = Q(status=Study.Status.REJECTED, since__lt=months_before(timezone.localdate(), Institution.get().retention_months))  # an old rejection is nobody's business
        seen |= Q(pk__in=person.within(Study.objects.filter(status__in=Study.APPLICATION).exclude(lapsed), 'view_applications').values('pk'))
        rating = Study.objects.filter(status=Study.Status.APPLIED, participations__course__kind=Course.Kind.AUDITION, participations__course__staff=person)
        seen |= Q(pk__in=rating.values('pk'))  # applications one rates, to their page for the jury
        studies = find(Study.objects.filter(seen).select_related('person', 'program', 'subject'), q, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__matriculation', 'person__email', 'subject__name', starts=True)  # by name, or by Hauptfach: 'trombone'
        groups.append(('Studierende und Bewerbungen', people(studies, limit, person)))
    if 'teacher' in roles:  # whom one teaches outside one's class, by the course: their file is not one's own
        taught = (
            Participation.objects.filter(course__term=Term.current(), study__status=Study.Status.ENROLLED)
            .filter(Q(course__staff=person) | Q(study__ensembles__coach=person, study__ensembles__course=F('course'))).exclude(study__teacher=person)
        )
        taught, first = Participation.objects.filter(pk__in=taught.values('pk')), {}  # so that SQLite looks up the rows found first
        for p in find(taught.select_related('study__person', 'course'), q, 'study__person__first_name', 'study__person__last_name', starts=True)[:limit * 5]:
            first.setdefault(p.study.person_id, p)
        groups.append(('In Ihrem Unterricht', [hit(p.study.person.name, p.course.get_absolute_url(), p.course.title) for p in list(first.values())[:limit]]))
    inside = bool(roles - OUTSIDE)
    if inside:  # whom to ask: who teaches or holds an office, on their page in Personen und Ämter
        lessons = Prefetch('teaching', Course.objects.filter(kind=Course.Kind.LESSON, subject__isnull=False).select_related('subject'), to_attr='lessons')
        known = staff().select_related('institute').prefetch_related(lessons)
        named = ranked(known, q, 'first_name', 'last_name', limit=limit)
        # then who teaches the Hauptfach sought in Einzelunterricht ('trombone': Posaune), not who sits on its commissions
        subjects = find(Subject.objects.all(), q, 'name', starts=True)
        teaching = known.filter(teaching__kind=Course.Kind.LESSON, teaching__subject__in=subjects).exclude(pk__in=[p.pk for p in named]).order_by('last_name')
        groups.append(('Personen', [
            hit(str(p), reverse('colleague', args=[p.pk]), ' · '.join(filter(None, [p.institute and str(p.institute), ', '.join(sorted({str(c.subject) for c in p.lessons})), p.email])))
            for p in [*named, *teaching[:limit - len(named)]]
        ]))
    offices = []  # and the offices: for applicants and alumni by their shared address only, without who works there
    for o in ranked(Office.objects.prefetch_related('user_set', 'institutes', 'programs'), q, 'name', limit=limit, names='amt'):
        members = [p for p in o.user_set.all() if p.is_active] if inside else []
        if inside or o.email:
            offices.append(hit(o.name + limited(o), reverse('office', args=[o.pk]), ' · '.join(filter(None, [o.email, ', '.join(f'{p.first_name} {p.last_name}' for p in members)]))))
    groups.append(('Ämter', offices))
    teaching = [Course.Kind.COURSE, Course.Kind.LESSON]
    courses = find(Course.objects.filter(term__end__gte=timezone.localdate(), term__in=Term.shown(person)).select_related('term'), q, 'title', 'staff__last_name', 'subject__name', starts=True)
    own = Exists(Module.objects.filter(courses=OuterRef('pk'), program__studies__person=person, program__studies__status=Study.Status.ENROLLED))  # one's own Studiengang's first
    groups.append(('Lehrveranstaltungen', [
        hit(c.title, c.get_absolute_url(), f'{c.get_kind_display()} · {c.term}') for c in courses.filter(kind__in=teaching).annotate(own=own).order_by('_fit', '-own', 'title')[:limit]
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
    if inside:
        groups.append(('Räume', [
            hit(r.name, reverse('rooms:room', args=[r.pk]), ' · '.join(filter(None, [r.purpose, r.building, f'{r.capacity} Plätze'])))
            for r in ranked(Room.objects.all(), q, 'name', 'purpose', 'building', 'equipment', limit=limit, names='raum')
        ]))
        # bookings by title or category: concerts for everyone, one's own dates, everything for the offices
        shown = Booking.objects.filter(end__gte=timezone.now()).select_related('room', 'course', 'category')
        if not person.everywhere('view_bookings'):
            shown = shown.filter(Q(category__public=True) | Q(pk__in=agenda(person).values('pk')) | Q(room__in=person.reached(Room, 'view_bookings')))
        named = [c.key for c in BookingCategory.objects.all() if all(w in fold(c.name) for w in sought)]
        events = shown.filter(Q(pk__in=find(shown, q, 'title', 'description', 'course__title').values('pk')) | Q(category__in=named))
        nearest = {}  # a course's or series' next date only, not every lesson of the term
        for b in events.order_by('start')[:limit * 20]:
            nearest.setdefault(b.course_id or b.series or b.pk, b)
        groups.append(('Termine', [
            hit(b.label, reverse('rooms:booking', args=[b.pk]), f'{date_format(timezone.localtime(b.start), "D j.n., H:i")} · {b.room} · {b.category}')
            for b in list(nearest.values())[:limit]
        ]))
    groups.append(('Postfach', [hit(m.subject, m.get_absolute_url(), date_format(m.created, 'j.n.Y')) for m in matching(person.messages.all(), q, 'subject', 'body')[:limit]]))
    if weak:
        groups.append(groups.pop(groups.index(functions)))
    if not any(forms <= ACTS for _, forms in meaning):  # 'new piano course': the page that does it stays first
        named = {label for _, forms in meaning for kind, label in FIRST.items() if stem(fold(kind)) in forms}
        groups.sort(key=lambda group: group[0] not in named)
    return groups


def jumps(roles, day, time):
    """Where a typed date can take a person: the room plan, free rooms at that time, the week's timetable."""
    label = date_format(day, 'l, j. F Y')
    found = [hit(f'Raumplan am {label}', f"{reverse('rooms:index')}?tag={day:%Y-%m-%d}", 'Räume')]
    if time:
        end = (datetime.combine(day, time) + timedelta(hours=1)).time()
        found.append(hit(f'Freie Räume am {label}, {time:%H:%M}–{end:%H:%M} Uhr', f"{reverse('rooms:index')}?tag={day:%Y-%m-%d}&von={time:%H:%M}&bis={end:%H:%M}", 'Räume'))
    if roles & {'teacher', 'student'}:
        found.append(hit(f'Stundenplan, Woche vom {date_format(day - timedelta(days=day.weekday()), "j. F")}', f"{reverse('courses:timetable')}?tag={day:%Y-%m-%d}", 'Lehre'))
    return found
