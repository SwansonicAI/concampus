from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError, RestrictedError
from django.forms import modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from core.access import kept, needs, require
from core.keys import Edition, Key, review
from core.models import Export, Grant, Membership, Person, SignIn, Text
from core.text import matching
from core.texts import TEXTS, Values, basics, mail, wording
from core.views import SHOWN
from .editors import EDITORS, formfield


@require('manage_accounts')
def approvals(request):
    """Rights given and offices joined that wait for a second person: whoever entered one cannot confirm it."""
    if request.method == 'POST':
        model = {'recht': Grant, 'mitglied': Membership}.get(request.POST.get('art'))
        mine = {'confirmed_by': None} if model is Membership else {}
        pending = get_object_or_404(model.objects.exclude(by=request.user).exclude(**({'person': request.user} if model is Membership else {})), pk=request.POST.get('id'), **mine) if model else None
        if pending is None:
            raise PermissionDenied('Bestätigen kann nur eine zweite Person.')
        if request.POST.get('decision') == 'yes':
            pending.confirm(request.user)
            messages.success(request, f'Bestätigt: {pending}.')
        else:
            pending.delete()
            messages.success(request, f'Abgelehnt: {pending}.')
        return redirect('setup:approvals')
    return render(request, 'setup/approvals.html', {
        'grants': Grant.objects.select_related('office', 'by'), 'members': Membership.objects.filter(confirmed_by=None).select_related('person', 'office', 'by'),
    })


@require('manage_accounts')
def exports(request):
    """Lists with personal data taken out as files, newest first: for whoever answers for access, the last thousand."""
    return render(request, 'setup/exports.html', {'exports': Export.objects.select_related('person')[:1000]})


@require('manage_accounts')
def sign_ins(request):
    """Sign-ins and failed tries, newest first, the last thousand: to see an account being guessed at or used by another."""
    return render(request, 'setup/sign_ins.html', {'sign_ins': SignIn.objects.select_related('person')[:1000], 'keep': SignIn.KEEP.days})


def usable(person):
    """The settings lists a person keeps up, for the start page and the tile."""
    return [e for e in EDITORS.values() if e.section and e.usable(person)]


def places(person):
    """(section, title, url, count) of everything under Einstellungen the person keeps up."""
    found = [(e.section, e.title, reverse('setup:list', args=[e.key]), e.objects(person).count()) for e in usable(person)]
    if 'manage_institution' in person.roles:
        found.append(('Verwaltung', 'Texte', reverse('setup:texts'), len(TEXTS)))
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
        raise PermissionDenied(needs([found.capability]))
    return found


def back(request, editor):
    """Where a saved form leads: the page it was opened from, else its list."""
    target = request.GET.get('next', '')
    if url_has_allowed_host_and_scheme(target, {request.get_host()}):
        return target
    return reverse('setup:list', args=[editor.key]) if editor.section else reverse('home')


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


def listing(request, key):
    e = editor(request, key)
    if not e.section:
        raise Http404
    q = request.GET.get('q', '').strip()
    objects = e.objects(request.user)
    objects = matching(objects, q, *e.search) if q else objects
    return render(request, 'setup/list.html', {
        'editor': e, 'q': q, 'rows': [(obj, [cell(obj, name) for _, name in e.columns]) for obj in objects[:SHOWN]],
        'count': objects.count(), 'history': e.model.visible_to(request.user.roles),
    })


def edit(request, key, pk=None):
    e = editor(request, key)
    if not (pk or e.creatable):
        raise Http404
    obj = get_object_or_404(e.objects(request.user), pk=pk) if pk else e.start(request)
    form = modelform_factory(e.model, form=e.form, fields=e.fields, formfield_callback=formfield)(request.POST or None, instance=obj)
    form.by = request.user  # who gives a right, which a second person confirms
    if e.sections:
        form.layout = e.sections
    if form.is_valid() and (saved := kept(form, request.user, e.capability)):
        obj = saved
        messages.success(request, f'„{obj}“ gespeichert.' + (' Neue Rechte und Mitgliedschaften gelten, sobald eine zweite Person sie unter „Freigaben“ bestätigt.' if getattr(form, 'pending', False) else ''))
        return redirect(back(request, e))
    return render(request, 'setup/form.html', {
        'editor': e, 'form': form, 'object': obj, 'back': back(request, e),
        'history': pk and bool(e.model.seen_by(request.user, pk)), 'table': e.model._meta.label_lower,
    })


class TextForm(forms.ModelForm):
    class Meta:
        model = Text
        fields = ['subject', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 10})}


@require('manage_institution')
def texts(request):
    """Every letter, message and text block, marked where the Hochschule has its own wording."""
    own = set(Text.objects.values_list('key', flat=True))
    groups = {}
    for key, spec in TEXTS.items():
        groups.setdefault(spec.group, []).append((key, spec, key in own))
    return render(request, 'setup/texts.html', {'groups': groups})


@require('manage_institution')
def text(request, key):
    spec = TEXTS.get(key)
    if spec is None:
        raise Http404
    record = Text.objects.filter(key=key).first() or Text(key=key, subject=spec.subject, body=spec.body)
    if request.method == 'POST' and 'standard' in request.POST:
        if record.pk:
            record.delete()
        messages.success(request, f'„{spec.title}“ steht wieder auf dem Standard.')
        return redirect('setup:texts')
    form = TextForm(request.POST or None, instance=record)
    if not spec.subject:
        del form.fields['subject']
    form.fields['body'].help_text = 'Platzhalter: ' + ', '.join('{' + name + '}' for name in spec.allowed)
    if form.is_valid():
        form.save()
        messages.success(request, f'„{spec.title}“ gespeichert.')
        return redirect('setup:texts')
    sample = Values(basics(), vorname='Mei Lin', nachname='Chen')  # the Hochschule's own data; other placeholders stay visible
    return render(request, 'setup/text.html', {
        'spec': spec, 'form': form, 'record': record, 'own': bool(record.pk),
        'preview': [part.format_map(sample) for part in wording(key)],  # as saved: the form may hold a broken draft
    })


@require('manage_accounts')
@require_POST
def invite(request, pk):
    """Send a person a link to set their password and sign in; it works once, for a few days."""
    person = get_object_or_404(Person, pk=pk, is_active=True)
    if not person.email:
        messages.error(request, 'Ohne E-Mail-Adresse kann keine Einladung verschickt werden.')
    else:
        link = request.build_absolute_uri(reverse('password_reset_confirm', args=[urlsafe_base64_encode(force_bytes(person.pk)), default_token_generator.make_token(person)]))
        mail('konto', person, zugang=link, benutzername=person.username)
        messages.success(request, f'Einladung an {person.email} verschickt.')
    return redirect(request.POST.get('next') if url_has_allowed_host_and_scheme(request.POST.get('next', ''), {request.get_host()}) else reverse('setup:edit', args=['konten', pk]))


@require_POST
def delete(request, key, pk):
    e = editor(request, key)
    if not e.deletable:
        raise Http404
    obj = get_object_or_404(e.objects(request.user), pk=pk)
    try:
        obj.delete()
    except (ProtectedError, RestrictedError):
        messages.error(request, f'„{obj}“ wird noch verwendet und bleibt bestehen.')
    else:
        messages.success(request, f'„{obj}“ gelöscht. Im Verlauf lässt es sich wiederherstellen.')
    return redirect(back(request, e))
