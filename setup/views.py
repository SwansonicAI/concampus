from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError, RestrictedError
from django.forms import modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.text import matching
from .editors import EDITORS, formfield


def usable(person):
    """The settings lists a person keeps up, for the start page and the tile."""
    return [e for e in EDITORS.values() if e.section and e.capability in person.roles]


def editor(request, key):
    found = EDITORS.get(key)
    if found is None:
        raise Http404
    if found.capability not in request.user.roles:
        raise PermissionDenied
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
    return value() if callable(value) else value


def index(request):
    editors = usable(request.user)
    if not editors:
        raise PermissionDenied
    sections = {}
    for e in editors:
        sections.setdefault(e.section, []).append((e, e.objects().count()))
    return render(request, 'setup/index.html', {'sections': sections})


def listing(request, key):
    e = editor(request, key)
    if not e.section:
        raise Http404
    q = request.GET.get('q', '').strip()
    objects = matching(e.objects(), q, *e.search) if q else e.objects()
    return render(request, 'setup/list.html', {
        'editor': e, 'q': q, 'rows': [(obj, [cell(obj, name) for _, name in e.columns]) for obj in objects[:500]],
        'count': objects.count(), 'history': e.model.visible_to(request.user.roles),
    })


def edit(request, key, pk=None):
    e = editor(request, key)
    if not (pk or e.creatable):
        raise Http404
    obj = get_object_or_404(e.objects(), pk=pk) if pk else e.start(request)
    form = modelform_factory(e.model, form=e.form, fields=e.fields, formfield_callback=formfield)(request.POST or None, instance=obj)
    if form.is_valid():
        obj = form.save()
        messages.success(request, f'„{obj}“ gespeichert.')
        return redirect(back(request, e))
    return render(request, 'setup/form.html', {
        'editor': e, 'form': form, 'object': obj, 'back': back(request, e),
        'history': pk and e.model.visible_to(request.user.roles), 'table': e.model._meta.label_lower,
    })


@require_POST
def delete(request, key, pk):
    e = editor(request, key)
    if not e.deletable:
        raise Http404
    obj = get_object_or_404(e.objects(), pk=pk)
    try:
        obj.delete()
    except (ProtectedError, RestrictedError):
        messages.error(request, f'„{obj}“ wird noch verwendet und bleibt bestehen.')
    else:
        messages.success(request, f'„{obj}“ gelöscht. Im Verlauf lässt es sich wiederherstellen.')
    return redirect(back(request, e))
