from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import Permission
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.db.models import F, Max, ProtectedError, RestrictedError
from django.forms import modelform_factory
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from core.access import CAPABILITIES, CODENAMES, EVERYWHERE, LOCK, SENSITIVE, SHORT, Refused, areas, kept, require
from core.forms import offered
from core.history import because
from core.keys import Edition, Key, review
from core import transfer
from core.apps import plural
from core.models import AccessRequest, Export, Feedback, Grant, Institute, Membership, Office, Person, SignIn, Term, limited, waiting
from core.text import collated, matching
from core.texts import mail
from core.shortcuts import SHOWN
from .editors import EDITORS, formfield


@require('manage_accounts')
def approvals(request):
    """Berechtigungen asked for, to grant through an office that holds them or turn down saying why; and sensitive ones
    given and offices joined that wait for a second person: whoever entered one cannot confirm it, nobody their own."""
    if request.POST.get('art') == 'antrag':
        asked = get_object_or_404(AccessRequest.objects.exclude(person=request.user).select_related('person'), pk=request.POST.get('id'))
        if request.POST.get('decision') == 'yes':
            office = get_object_or_404(holding(asked.capability), pk=request.POST.get('amt'))
            later = asked.grant(office, request.user)
            messages.success(request, f'{asked.person} gehört zum Amt {office}' + (', sobald eine zweite Person es bestätigt.' if later else '.'))
        elif why := request.POST.get('begruendung', '').strip():
            asked.refuse(request.user, why[:300])
            messages.success(request, f'Abgelehnt: {asked}.')
        else:
            messages.error(request, 'Bitte angeben, warum nicht.')
        return redirect('setup:approvals')
    if request.method == 'POST':
        model ={'recht': Grant, 'mitglied': Membership}.get(request.POST.get('art'))
        mine = {'confirmed_by': None} if model is Membership else {}
        own = {'person': request.user} if model is Membership else {'office__in': request.user.groups.values('pk')}  # never rights one gets oneself
        pending = get_object_or_404(model.objects.exclude(by=request.user).exclude(**own), pk=request.POST.get('id'), **mine) if model else None
        if pending is None:
            raise PermissionDenied('Bestätigen kann nur eine zweite Person.')
        if request.POST.get('decision') == 'yes':
            pending.confirm(request.user)
            messages.success(request, f'Bestätigt: {pending}.')
        else:
            pending.delete()
            messages.success(request, f'Abgelehnt: {pending}.')
        return redirect('setup:approvals')
    asks = list(AccessRequest.objects.select_related('person'))
    labels = dict(c for _, group in CAPABILITIES for c in group)
    for a in asks:  # the offices that hold it, those for the person's own Institut first
        a.short, a.label = SHORT.get(a.capability, a.capability), labels.get(a.capability, '')
        a.offices = sorted(holding(a.capability), key=lambda o: (a.person.institute_id not in {i.pk for i in o.institutes.all()}, collated(o.name)))
    grants, givers = list(Grant.objects.select_related('office', 'by')), Person.holding('manage_accounts')
    for g in grants:  # nobody may confirm rights for their own office: where all who give rights are in it, nobody can
        g.nobody = not givers.exclude(pk=g.by_id).exclude(groups=g.office).exists()
    return render(request, 'setup/approvals.html', {
        'asks': asks, 'grants': grants, 'own': set(request.user.groups.values_list('pk', flat=True)), 'members': Membership.objects.filter(confirmed_by=None).select_related('person', 'office', 'by'),
    })


def holding(capability):
    return Office.objects.filter(permissions__codename=capability).prefetch_related('permissions', 'institutes', 'programs').distinct()


@require('manage_accounts')
def rights(request):
    """Who may do what, as a table: a row for each Berechtigung by area, a column for each office with who is in it, a
    square where it holds it. A click on a square takes it away or gives it, a sensitive one once a second person
    confirms it; on one that waits, it withdraws it."""
    if request.method == 'POST':
        pk, _, c = request.POST.get('feld', '').partition(' ')
        if c not in CODENAMES or not pk.isdigit():
            raise Http404
        office = get_object_or_404(Office.objects.prefetch_related('permissions', 'institutes', 'programs', 'grants'), pk=pk)
        right, short = Permission.objects.get(content_type__model='office', codename=c), f'{office}: „{SHORT[c]}“'
        grant = next((g for g in office.grants.all() if c in g.rights), None)
        if c in office.marks:
            office.permissions.remove(right)
            messages.success(request, f'{short} entzogen.')
        elif grant:
            grant.rights.remove(c)
            if grant.rights:
                grant.save()
            else:
                grant.delete()
            messages.success(request, f'{short} nicht mehr beantragt.')
        elif c in EVERYWHERE and office.mark == 'part':
            messages.error(request, f'„{SHORT[c]}“ gilt nur für die ganze Hochschule, {office} ist nur für einen Teil zuständig.')
        elif waiting([c], request.user):
            Grant.objects.create(office=office, rights=[c], by=request.user)
            messages.success(request, f'{short} gilt, sobald eine zweite Person es unter „Freigaben“ bestätigt.')
        else:
            office.permissions.add(right)
            messages.success(request, f'{short} gegeben.')
        return redirect(reverse('setup:rights') + f'#{c}')
    offices = list(Office.staffed().prefetch_related('grants'))
    for o in offices:
        o.limit, o.people = limited(o), [*o.user_set.all(), *(m.person for m in o.standing_in)]
        o.wait = {c for g in o.grants.all() for c in g.rights}
    table = {c: [(o, o.marks.get(c) or ('wait' if c in o.wait else '')) for o in offices] for c in CODENAMES}
    return render(request, 'setup/rights.html', {
        'offices': offices, 'areas': areas(table), 'nobody': {c for c, cells in table.items() if not any(m for _, m in cells)}, 'sensitive': SENSITIVE, 'lock': LOCK,
    })


@require('manage_accounts')
def exports(request):
    """Lists with personal data taken out as files, newest first: for whoever answers for access, the last thousand."""
    return render(request, 'setup/exports.html', {'exports': Export.objects.select_related('person')[:1000]})


@require('manage_institution')
def feedback(request):
    """Barriers reported under the accessibility statement, open ones first, each with the day it must be answered by."""
    if request.method == 'POST':
        Feedback.objects.filter(pk=request.POST.get('beantwortet'), answered=None).update(answered=timezone.localdate(), answered_by=request.user)
        return redirect('setup:feedback')
    return render(request, 'setup/feedback.html', {'reports': Feedback.objects.order_by(F('answered').desc(nulls_first=True), 'received')[:300]})


@require('manage_accounts')
def sign_ins(request):
    """Sign-ins and failed tries, newest first, the last thousand: to see an account being guessed at or used by another;
    by name or address, failures alone on asking."""
    q, failed = request.GET.get('q', '').strip(), request.GET.get('nur') == 'fehlversuche'
    found = SignIn.objects.select_related('person').filter(success=False) if failed else SignIn.objects.select_related('person')
    if q:
        found = matching(found, q, 'username', 'person__first_name', 'person__last_name', 'ip')
    return render(request, 'setup/sign_ins.html', {'sign_ins': found[:1000], 'keep': SignIn.KEEP.days, 'q': q, 'failed': failed})


def usable(person):
    """The settings lists a person keeps up, for the start page and the tile."""
    return [e for e in EDITORS.values() if e.section and e.usable(person)]


def places(person):
    """(section, title, url, count) of everything under Einstellungen the person keeps up."""
    found = [(e.section, e.title, reverse('setup:list', args=[e.key]), e.objects(person).count()) for e in usable(person)]
    if 'manage_accounts' in person.roles:
        found.append(('Verwaltung', 'Berechtigungen im Überblick', reverse('setup:rights'), len(CODENAMES)))
    if person.roles & {'manage_institution', 'manage_students'}:
        found.append(('Verwaltung', 'Amtliche Verzeichnisse', reverse('setup:keys'), len(Key.List)))
    return found


@require('manage_institution', 'manage_students')
def official(request):
    """The official key lists in use, the editions each came in with and what they changed; for the student office, who
    lives where a change of Kreis leaves in doubt."""
    lists = []
    for listing, name in Key.List.choices:
        entries = Key.objects.filter(listing=listing)
        lists.append((name, Edition.objects.filter(listing=listing).order_by('-pk').first(), entries.filter(until=None).count(), entries.exclude(until=None).count()))
    people, gave = review()
    for towns in gave.values():
        for town in towns:
            town.left = town.until + timedelta(days=1)  # the first day in its new Kreis
    return render(request, 'setup/keys.html', {
        'lists': lists, 'editions': Edition.objects.exclude(notes=''), 'people': people if 'manage_students' in request.user.roles else None,
        'count': people.count(), 'gave': [(code, Key.objects.filter(listing=Key.List.KREIS, code=code).first(), towns) for code, towns in gave.items()],
    })


def editor(request, key):
    found = EDITORS.get(key)
    if found is None:
        raise Http404
    if not found.usable(request.user):
        raise Refused([found.capability])
    return found


def back(request, editor):
    """Where a saved form leads: the page it was opened from, else its list."""
    target = request.GET.get('next', '')
    if url_has_allowed_host_and_scheme(target, {request.get_host()}):
        return target
    return reverse('setup:list', args=[editor.key]) if editor.section else reverse(editor.home or 'home')


def cell(obj, name):
    value = getattr(obj, f'get_{name}_display', None) or getattr(obj, name)
    if hasattr(value, 'all'):
        return ', '.join(map(str, value.all())) or '–'
    if isinstance(value, bool):
        return 'ja' if value else ''
    return value() if callable(value) else value


def index(request):
    sections = {}
    for section, *place in places(request.user):
        sections.setdefault(section, []).append(place)
    if not sections:
        raise PermissionDenied
    return render(request, 'setup/index.html', {'sections': sections})


def listing(request, key, own=False):
    """An editor's list: under Einstellungen, or `own`, as a page of the area it belongs to (Editor.home)."""
    e = editor(request, key)
    if not (e.section or own):
        raise Http404
    q = request.GET.get('q', '').strip()
    objects = e.objects(request.user)
    objects = matching(objects, q, *e.search) if q else objects
    return render(request, 'setup/list.html', {
        'editor': e, 'q': q, 'rows': [(obj, [cell(obj, name) for _, name in e.columns]) for obj in objects[:SHOWN]],
        'count': objects.count(), 'history': e.model.visible_to(request.user.roles),
    })


def uses(obj):
    """What still hangs on a record, in words: its own `used()` where it has one, else every record that points at it;
    empty when it may go. Nothing that depends on it is cleared or deleted with it."""
    if hasattr(obj, 'used'):
        return obj.used()
    found = []
    for rel in obj._meta.related_objects:
        if rel.one_to_many or rel.one_to_one:
            n = rel.related_model._base_manager.filter(**{rel.field.name: obj}).count()
            meta = rel.related_model._meta
            if n:
                found.append(f'{n} {meta.verbose_name if n == 1 else meta.verbose_name_plural}')
    return ', '.join(found)


@require_POST
def move(request, key, pk):
    """A record of a list kept in order one place up or down, the list numbered anew from 1."""
    e = editor(request, key)
    if not e.ordered:
        raise Http404
    rows = list(e.objects(request.user))
    at = next((n for n, obj in enumerate(rows) if obj.pk == pk), None)
    if at is None:
        raise Http404
    to = at + (-1 if request.POST.get('step') == '-1' else 1)
    if 0 <= to < len(rows):
        rows.insert(to, rows.pop(at))
        for n, obj in enumerate(rows, 1):
            if obj.order != n:
                obj.order = n
                obj.save(update_fields=['order'])
    came = request.META.get('HTTP_REFERER', '')  # the list it was moved in: under Einstellungen, or the area's own page
    return redirect(came if url_has_allowed_host_and_scheme(came, {request.get_host()}) else reverse(e.home) if e.home else reverse('setup:list', args=[key]))


def save(form, person, capability):
    """kept(), with the reason a form asks for a change kept in the Verlauf."""
    with because(form.cleaned_data.get('reason', '')):
        return kept(form, person, capability)


def edit(request, key, pk=None):
    e = editor(request, key)
    if not (pk or e.creatable):
        raise Http404
    obj = get_object_or_404(e.objects(request.user), pk=pk) if pk else e.start(request)
    if not pk and e.ordered:  # a new one goes to the end
        obj.order = (e.model.objects.aggregate(n=Max('order'))['n'] or 0) + 1
    form = modelform_factory(e.model, form=e.form, fields=e.fields, formfield_callback=formfield)(request.POST or None, request.FILES or None, instance=obj)
    form.by = request.user  # who gives a right, which a second person confirms
    form.query = request.GET  # what the page was opened for, like the semester of an Unterlagenart
    if e.sections:
        form.layout = e.sections
    for name, choice in form.fields.items():  # what is no longer offered stays only where it is already chosen
        if hasattr(choice, 'queryset') and any(f.name == 'retired' for f in choice.queryset.model._meta.fields):
            offered(choice, form.initial.get(name))
    field = form.fields.get('institute') or form.fields.get('institutes')
    if field and (ours := request.user.ours(e.capability)):  # an office for some Institute: only those, its own preset
        field.queryset = field.queryset.filter(pk__in=ours)
        if not obj.pk:
            form.initial |= {'institutes': list(ours)} if 'institutes' in form.fields else {'institute': next(iter(ours))}
    if form.is_valid() and (saved := save(form, request.user, e.capability)):
        obj = saved
        messages.success(request, f'„{obj}“ gespeichert.' + (' Sensible Berechtigungen gelten, sobald eine zweite Person sie unter „Freigaben“ bestätigt.' if getattr(form, 'pending', False) else ''))
        return redirect(back(request, e))
    return render(request, 'setup/form.html', {
        'editor': e, 'form': form, 'object': obj, 'back': back(request, e),
        'trail': [(None, reverse(e.home))] if e.home and not e.section else None,  # in the menu where its list is
        'history': pk and bool(e.model.seen_by(request.user, pk)), 'table': e.model._meta.label_lower,
        'used': uses(obj) if pk and e.deletable else '',
    })


@require('manage_staff')
def staff_import(request):
    """Teachers from a personnel system's export (core.transfer.staff): read first, each line shown new, changed with
    what, or at fault; loaded once confirmed, all or nothing. A Lehrverpflichtung goes to the semester being planned, else
    this one; an office for some Institute loads only theirs. ?vorlage: the headings to start a file from."""
    term = Term.upcoming() or Term.current()
    if 'vorlage' in request.GET:
        response = HttpResponse('\N{BYTE ORDER MARK}' + ';'.join(transfer.COLUMNS) + '\r\n', content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="lehrende.csv"'
        return response
    if term and request.method == 'POST' and (upload := request.FILES.get('datei')):
        raw = upload.read()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:  # Excel's „CSV (Trennzeichen-getrennt)“ on a German Windows
            text = raw.decode('cp1252', errors='replace')
        request.session['lehrende'] = transfer.staff(text, term)
        return redirect('setup:staff_import')
    document = request.session.get('lehrende')
    if not (document and term):
        return redirect('courses:staff')
    ours = None if request.user.everywhere('manage_staff') else set(Institute.objects.filter(pk__in=request.user.ours('manage_staff')).values_list('name', flat=True))
    faults = [f'Zeile {i + 2}: Institut {p.get("institute") or "fehlt"}, außerhalb Ihres Amtes' for i, p in enumerate(document['people']) if ours is not None and p.get('institute') not in ours]
    faults += transfer.check(document)
    found, mine = transfer.changes(document), set(request.user.within(Person.teachers(), 'manage_staff').values_list('pk', flat=True))
    for i, (p, person, _) in enumerate(found):  # who is there already, matched across the Hochschule: only one's own teachers change
        if person is None:
            continue
        if person.pk not in mine and Person.teachers().filter(pk=person.pk).exists():
            faults.append(f'Zeile {i + 2}: {person} lehrt in einem Institut außerhalb Ihres Amtes')
        elif (person.reach or person.pk not in mine) and (p.get('email', person.email).lower() != person.email.lower() or p.get('username', person.username) != person.username):
            faults.append(f'Zeile {i + 2}: E-Mail und Benutzername von {person} ändert nur die Kontenverwaltung')
    if request.method == 'POST' and 'laden' in request.POST and not faults:
        counts = transfer.load(document)
        del request.session['lehrende']
        messages.success(request, 'Geladen: ' + ', '.join(f'{n} {what}' for what, n in counts.items()) + '.')
        return redirect('courses:staff')
    kinds = dict(Person.Employment.choices)
    rows = [
        {'line': i + 2, 'p': p, 'person': person, 'changed': changed, 'kind': kinds.get(p.get('employment'), p.get('employment', '')),
         'hours': next((o['hours'] for o in p.get('obligations', [])), ''), 'faults': [f.split(': ', 1)[1] for f in faults if f.startswith(f'Zeile {i + 2}:')]}
        for i, (p, person, changed) in enumerate(found)
    ]
    return render(request, 'setup/staff_import.html', {
        'term': term, 'rows': rows, 'faults': faults, 'new': sum(1 for r in rows if not r['person'] and not r['faults']),
        'changed': sum(1 for r in rows if r['person'] and r['changed'] and not r['faults']), 'same': sum(1 for r in rows if r['person'] and not r['changed'] and not r['faults']),
    })


@require('manage_accounts', 'manage_staff')
@require_POST
def invite(request, pk):
    """Send a person a link to set their password and sign in; it works once, for a few days. Whoever takes teachers on
    invites the teachers in their reach."""
    people = Person.objects.all() if 'manage_accounts' in request.user.roles else request.user.within(Person.teachers(), 'manage_staff')
    person = get_object_or_404(people, pk=pk, is_active=True)
    if not person.email:
        messages.error(request, 'Ohne E-Mail-Adresse kann keine Einladung verschickt werden.')
    else:
        invitation(request, person)
        messages.success(request, f'Einladung an {person.email} verschickt.')
    return redirect(request.POST.get('next') if url_has_allowed_host_and_scheme(request.POST.get('next', ''), {request.get_host()}) else reverse('setup:edit', args=['konten', pk]))


def invitation(request, person):
    link = request.build_absolute_uri(reverse('password_reset_confirm', args=[urlsafe_base64_encode(force_bytes(person.pk)), default_token_generator.make_token(person)]))
    mail('konto', person, zugang=link, benutzername=person.username)


def uninvited(person):
    """The teachers in the person's reach who have never signed in, with an address to invite them at."""
    return person.within(Person.teachers(), 'manage_staff').filter(is_active=True, last_login=None).exclude(email='')


@require('manage_staff')
@require_POST
def invite_new(request):
    """Invite every teacher in reach who has never signed in, as after loading new ones from the personnel system."""
    people = list(uninvited(request.user))
    for person in people:
        invitation(request, person)
    messages.success(request, f'{plural(len(people), "Einladung", "Einladungen")} verschickt.')
    return redirect('courses:staff')


@require_POST
def delete(request, key, pk):
    e = editor(request, key)
    if not e.deletable:
        raise Http404
    obj = get_object_or_404(e.objects(request.user), pk=pk)
    if why := uses(obj):
        messages.error(request, f'„{obj}“ bleibt: daran hängen {why}.' + (f' Stattdessen „{obj._meta.get_field("retired").verbose_name}“ ankreuzen.' if hasattr(obj, 'retired') else ''))
        return redirect(back(request, e))
    try:
        obj.delete()
    except (ProtectedError, RestrictedError):
        messages.error(request, f'„{obj}“ wird noch verwendet und bleibt bestehen.')
    else:
        messages.success(request, f'„{obj}“ gelöscht. Im Verlauf lässt es sich wiederherstellen.')
    return redirect(back(request, e))
