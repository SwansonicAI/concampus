"""The change history: every change to a tracked record, kept as the fields that changed, before and after, with who made
it and when. Records save and delete themselves into it by signals; bulk writes, which send none, call `saved()`.
Deleting a person deletes their history with them, as data protection asks."""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models, transaction
from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.utils import timezone
from django.utils.formats import localize
from django.utils.text import capfirst

from .models import Change, Person, Tracked

SKIP = {'last_login'}  # changes nobody needs to retrace
SECRET = {'password'}  # that it changed is kept, not the value

actor = ContextVar('actor', default=None)
silent = ContextVar('silent', default=False)


@contextmanager
def acting(person):
    """Changes made inside are made by `person`: the request's user, or whom a command acts for."""
    token = actor.set(person)
    try:
        yield
    finally:
        actor.reset(token)


@contextmanager
def quietly():
    """Changes made inside are not recorded: the demo data."""
    token = silent.set(True)
    try:
        yield
    finally:
        silent.reset(token)


def tracked():
    return [model for model in apps.get_models() if issubclass(model, Tracked)]


def tables(roles):
    """The tables whose history these roles see."""
    return [model._meta.label_lower for model in tracked() if model.visible_to(roles)]


def fields(model):
    return [f for f in model._meta.concrete_fields if not f.primary_key and f.attname not in SKIP]


def plain(field, value):
    """A value as the history compares and keeps it."""
    return None if value is None else field.get_prep_value(value)


def current(obj, name):
    """A field's or link's value now, as the history keeps it."""
    field = obj._meta.get_field(name)
    if field.many_to_many:
        return sorted(getattr(obj, name).values_list('pk', flat=True))
    return plain(field, getattr(obj, field.attname))


def kept(field, value):
    """A kept value back in the form `current` gives."""
    return value if field.many_to_many else plain(field, field.to_python(value))


def owner(obj):
    """The id of the person the record is about, by its `owner` path."""
    if not obj.owner:
        return None
    for name in obj.owner.split('.'):
        obj = getattr(obj, name, None)
    return obj


def entry(obj, action, values):
    return Change(
        by_id=getattr(actor.get(), 'pk', None), person_id=owner(obj), table=obj._meta.label_lower, row=obj.pk,
        action=Change.Action.RESTORED if getattr(obj, '_restoring', False) else action, fields=values,
    )


def saved(objs):
    """Record what saving these records changed: against what was loaded, or their creation."""
    if silent.get():
        return
    entries = []
    for obj in objs:
        loaded = getattr(obj, '_loaded', None)
        now = {f: plain(f, getattr(obj, f.attname)) for f in fields(type(obj)) if loaded is None or f.attname in loaded}
        if loaded is None:
            entries.append(entry(obj, Change.Action.CREATED, {}))
        else:
            pairs = {f.attname: [plain(f, loaded[f.attname]), value] for f, value in now.items()}
            if diff := {name: [None, None] if name in SECRET else pair for name, pair in pairs.items() if pair[0] != pair[1]}:
                entries.append(entry(obj, Change.Action.CHANGED, diff))
        obj._loaded = {f.attname: value for f, value in now.items()}
    Change.objects.bulk_create(entries)


def on_save(sender, instance, raw, **kwargs):
    if not raw:
        saved([instance])


def on_delete(sender, instance, origin, **kwargs):
    """Keep all of a deleted record, its links included, so it can be restored; unless a person is deleted."""
    source = origin.model if isinstance(origin, models.QuerySet) else type(origin)
    if silent.get() or issubclass(source, Person):
        return
    values = {f.attname: plain(f, getattr(instance, f.attname)) for f in fields(sender) if f.attname not in SECRET}
    values |= {f.name: current(instance, f.name) for f in sender._meta.many_to_many}
    entry(instance, Change.Action.DELETED, values).save()


def on_link(sender, instance, action, reverse, pk_set, **kwargs):
    """Links (participants, staff, groups) are kept as the lists before and after."""
    if silent.get() or reverse or not isinstance(instance, Tracked) or action not in ('pre_add', 'pre_remove', 'pre_clear'):
        return  # a plain Group shares its permission links with Office
    name = next(f.name for f in instance._meta.many_to_many if f.remote_field.through is sender)
    before = current(instance, name)
    after = sorted(set(before) | pk_set if action == 'pre_add' else set(before) - pk_set if action == 'pre_remove' else [])
    if after != before:
        entry(instance, Change.Action.CHANGED, {name: [before, after]}).save()


def connect():
    for model in tracked():
        post_save.connect(on_save, sender=model)
        pre_delete.connect(on_delete, sender=model)
        for field in model._meta.many_to_many:
            m2m_changed.connect(on_link, sender=field.remote_field.through)


def restore(change):
    """Undo a change by a change of its own, or bring a deleted record back. Raises ValidationError where that no longer
    fits: the record has changed again since, is gone, or would clash."""
    model = apps.get_model(change.table)
    meta, deleted = model._meta, change.action == Change.Action.DELETED
    if deleted:
        if model.objects.filter(pk=change.row).exists():
            raise ValidationError('Der Datensatz besteht bereits wieder.')
        obj, values = model(pk=change.row), change.fields
    else:
        obj = model.objects.filter(pk=change.row).first()
        if obj is None:
            raise ValidationError('Der Datensatz ist inzwischen gelöscht.')
        values = {name: before for name, (before, _) in change.fields.items() if name not in SECRET}
        if any(current(obj, name) != kept(meta.get_field(name), change.fields[name][1]) for name in values):
            raise ValidationError('Seitdem erneut geändert. Bitte zuerst die spätere Änderung zurücksetzen.')
    links = {}
    for name, before in values.items():
        field = meta.get_field(name)
        if field.many_to_many:
            links[field] = before
        else:
            setattr(obj, field.attname, field.to_python(before))
    obj.full_clean(exclude=None if deleted else [f.name for f in meta.fields if f.attname not in values])
    obj._restoring = True
    with transaction.atomic():
        obj.save(force_insert=deleted)
        for field, pks in links.items():
            getattr(obj, field.name).set(field.related_model.objects.filter(pk__in=pks))


def describe(changes):
    """Changes ready to show, each with `record` (what was changed), `lines` [(label, before, after)] and `restorable`."""
    changes, rows, linked = list(changes), defaultdict(set), defaultdict(set)
    for c in changes:
        c.model = apps.get_model(c.table)
        rows[c.model].add(c.row)
        if c.action != Change.Action.DELETED:
            for name, pair in c.fields.items():
                if (field := c.model._meta.get_field(name)).is_relation:
                    linked[field.related_model, target(field)] |= {pk for v in pair for pk in (v if isinstance(v, list) else [v]) if pk is not None}
    records = {model: model.objects.select_related().in_bulk(pks) for model, pks in rows.items()}
    names = {
        (model, to): {getattr(o, to): str(o) for o in model.objects.select_related().filter(**{f'{to}__in': pks})}
        for (model, to), pks in linked.items()
    }
    labels = {(c.model, c.row): gone(c) for c in changes if c.action == Change.Action.DELETED and c.row not in records[c.model]}
    for c in changes:
        exists = c.row in records[c.model]
        record = records[c.model][c.row] if exists else labels.get((c.model, c.row))
        c.record = f'{capfirst(c.model._meta.verbose_name)} {record or f"Nr. {c.row}"}'
        c.lines = [] if c.action == Change.Action.DELETED else [line(c.model._meta.get_field(name), pair, names) for name, pair in c.fields.items()]
        if c.action == Change.Action.DELETED:
            c.restorable = not exists
        else:
            c.restorable = exists and c.action != Change.Action.CREATED and bool(c.fields.keys() - SECRET)
    return changes


def target(field):
    """What a relation stores of the record it points to: its key for lists referred to by key, else its id."""
    return 'pk' if field.many_to_many else field.target_field.attname


def gone(change):
    """A deleted record's name, from what was kept of it."""
    try:
        return str(change.model(**{f.attname: f.to_python(change.fields[f.attname]) for f in fields(change.model) if f.attname in change.fields}))
    except ObjectDoesNotExist:  # it named records deleted with it
        return None


def line(field, pair, names):
    if field.attname in SECRET:
        return capfirst(field.verbose_name), '', 'neu gesetzt'
    return capfirst(field.verbose_name), *(show(field, v, names) for v in pair)


def show(field, value, names):
    """A kept value as people read it."""
    if field.is_relation:
        pks = value if isinstance(value, list) else [value]
        return ', '.join(names[field.related_model, target(field)].get(pk, f'Nr. {pk} (gelöscht)') for pk in pks if pk is not None) or '–'
    if value in (None, ''):
        return '–'
    if field.choices:
        return dict(field.flatchoices).get(value, value)
    value = field.to_python(value)
    if isinstance(value, bool):
        return 'ja' if value else 'nein'
    if isinstance(value, datetime):
        return f'{timezone.localtime(value):%d.%m.%Y, %H:%M}'
    if isinstance(value, date):
        return f'{value:%d.%m.%Y}'
    return localize(value.normalize() if isinstance(value, Decimal) else value)  # 1,7 rather than 1,70
