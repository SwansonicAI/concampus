"""The official key lists as data: Länder, Kreise, Staaten, Hochschulen, Studienfächer and Abschlussprüfungen of the
Statistisches Bundesamt's Schlüsselverzeichnis for the student and exam statistics, and the Gemeinden of its
Gemeindeverzeichnis. Lists change: Kreise merge, a town becomes kreisfrei, a Hochschule closes. An entry then ends, it
never vanishes, and names what replaced it. Records keep a code as it was given; the official report sends the code
valid now (replaced), and whoever lives in a town that moved to another Kreis moves with it (follow). The lists are kept
centrally: `manage.py keys` brings in the editions shipped with an update (core/data), or a Gemeindeverzeichnis given."""
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

from django import forms
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format

from . import destatis

DATA = Path(__file__).resolve().parent / 'data'  # the editions shipped: the Gemeindeverzeichnis as Destatis publishes it

# What a field draws on: (list, the prefix of its codes in the official record, heading: None under its Land, '' none)
PLACES = (('kreis', '', None), ('staat', '99', 'Ausland'))  # where someone lives or gained the HZB: a Kreis, or 99 and a state
UNIVERSITIES = (('hochschule', '', 'In Deutschland'), ('staat', destatis.ABROAD, 'Im Ausland'))
COUNTRIES = (('staat', '', ''),)
DISTRICTS = (('kreis', '', None),)
GERMAN = (('hochschule', '', ''),)
FIELDS = (('fach', '', ''),)
DEGREES = (('abschluss', '', ''),)
TOWNS = (('gemeinde', '', ''),)
HOME = {'000', '997', '998', '999'}  # Germany, and no state at all: never a place abroad


class Key(models.Model):
    """An entry of an official key list. Ended, it stays, with the day it ended and the code that replaced it."""

    class List(models.TextChoices):
        LAND = 'land', 'Bundesländer'
        KREIS = 'kreis', 'Kreise und kreisfreie Städte'
        GEMEINDE = 'gemeinde', 'Gemeinden'
        STAAT = 'staat', 'Staaten'
        HOCHSCHULE = 'hochschule', 'Hochschulen'
        FACH = 'fach', 'Studienfächer'
        ABSCHLUSS = 'abschluss', 'Abschlussprüfungen'

    listing = models.CharField('Verzeichnis', max_length=10, choices=List)
    code = models.CharField('Schlüssel', max_length=8)
    name = models.CharField('Bezeichnung', max_length=200)
    within = models.CharField('Gehört zu', max_length=8, blank=True, help_text='Bei einem Kreis sein Land, bei einer Gemeinde ihr Kreis.')
    postcode = models.CharField('Postleitzahl', max_length=5, blank=True)
    since = models.DateField('Gültig ab', null=True, blank=True)
    until = models.DateField('Gültig bis', null=True, blank=True)
    successor = models.CharField('Ersetzt durch', max_length=8, blank=True)

    class Meta:
        app_label = 'core'
        ordering = ['name']
        verbose_name = 'Amtlicher Schlüssel'
        verbose_name_plural = 'Amtliche Schlüssel'
        constraints = [models.UniqueConstraint(fields=['listing', 'code'], name='unique_key')]
        indexes = [models.Index(fields=['listing', 'name'])]

    def __str__(self):
        return f'{self.name} ({self.code})'


class Edition(models.Model):
    """One edition of an official list brought in: which, of when, from where, and what it changed."""

    listing = models.CharField('Verzeichnis', max_length=10, choices=Key.List)
    name = models.CharField('Ausgabe', max_length=120)
    effective = models.DateField('Stand')
    source = models.CharField('Quelle', max_length=300, blank=True)
    imported = models.DateTimeField('Eingespielt', default=timezone.now)
    added = models.PositiveIntegerField('Neu', default=0)
    renamed = models.PositiveIntegerField('Geändert', default=0)
    ended = models.PositiveIntegerField('Beendet', default=0)
    notes = models.TextField('Änderungen', blank=True)

    class Meta:
        app_label = 'core'
        ordering = ['-effective', '-pk']
        verbose_name = 'Ausgabe eines Verzeichnisses'
        verbose_name_plural = 'Ausgaben der Verzeichnisse'

    def __str__(self):
        return f'{self.get_listing_display()}: {self.name}'


def find(keys, code):
    """The entry a code stands for in the lists `keys` draws on, ended or not; None if none."""
    for listing, prefix, _ in keys:
        if code and code.startswith(prefix) and (key := Key.objects.filter(listing=listing, code=code[len(prefix):]).first()):
            return key
    return None


def label(keys, code):
    """A code as people read it; one since ended says until when and what took its place."""
    key = find(keys, code)
    if key is None:
        return code
    if key.until is None:
        return key.name
    later = key.successor and Key.objects.filter(listing=key.listing, code=key.successor).first()
    return f'{key.name} (bis {date_format(key.until, "j.n.Y")}' + (f', heute {later.name}' if later else '') + ')'


def choices(keys):
    """The entries valid today, as a form offers them: Kreise under their Land, the states abroad under one heading."""
    lands, found = dict(Key.objects.filter(listing=Key.List.LAND).values_list('code', 'name')), []
    for listing, prefix, group in keys:
        rows = Key.objects.filter(listing=listing, until=None).exclude(code__in=HOME if prefix else ())
        if group is None:
            by = defaultdict(list)
            for key in rows:
                by[lands.get(key.within, '')].append((prefix + key.code, key.name))
            found += sorted(by.items())
        elif group:
            found.append((group, [(prefix + key.code, key.name) for key in rows]))
        else:
            found += [(prefix + key.code, key.name) for key in rows]
    return found


def replaced(keys):
    """{a code since ended: the code valid today, following each replacement, or '' where none took its place}: what the
    official report sends instead of a code as it was given."""
    found = {}
    for listing, prefix, _ in keys:
        entries = {key.code: key for key in Key.objects.filter(listing=listing)}
        for key in entries.values():
            if key.until:
                later, seen = key, {key.code}
                while later and later.until and later.successor and later.successor not in seen:
                    seen.add(later.successor)
                    later = entries.get(later.successor)
                found[prefix + key.code] = prefix + later.code if later and not later.until else ''
    return found


class KeySelect(forms.Select):
    """The entries valid today, and a code since ended where a record still holds it, named with when it ended."""

    def __init__(self, keys=(), attrs=None):
        super().__init__(attrs)
        self.keys = keys

    def optgroups(self, name, value, attrs=None):
        groups = super().optgroups(name, value, attrs)
        held = [v for v in value if v and not any(option['value'] == v for _, options, _ in groups for option in options)]
        for code in held:
            groups.insert(0, (None, [self.create_option(name, code, label(self.keys, code), True, 0, attrs=attrs)], 0))
        return groups


class KeyChoice(forms.ChoiceField):
    """A choice from official key lists: what is valid today; a code since ended, as a record holds it, stays valid."""

    def __init__(self, *, keys, max_length=None, empty_value=None, town=None, **kwargs):  # the last as a CharField's formfield passes them
        self.keys = keys
        attrs = {'data-town': town} if town else {}
        super().__init__(choices=lambda: [('', '–'), *choices(keys)], widget=KeySelect(keys, attrs), **kwargs)

    def valid_value(self, value):
        return find(self.keys, value) is not None


class TownInput(forms.HiddenInput):
    """A town found by searching it (app.js placed), its name along for the search box."""

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['attrs']['data-name'] = label(TOWNS, value) if value else ''
        return context


class KeyField(models.CharField):
    """A code of official key lists, kept as it was given: forms offer what is valid today, a code since ended stays and
    shows its name with when it ended and what replaced it (get_FOO_display). A place may come with its town (`town`),
    found by searching it."""

    def __init__(self, *args, keys, town=None, **kwargs):
        self.keys, self.town = keys, town
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        return name, path, args, {**kwargs, 'keys': self.keys, **({'town': self.town} if self.town else {})}

    def contribute_to_class(self, cls, name, *args, **kwargs):
        super().contribute_to_class(cls, name, *args, **kwargs)
        setattr(cls, f'get_{name}_display', lambda record: label(self.keys, getattr(record, self.attname)))

    def validate(self, value, record):
        super().validate(value, record)
        if value and find(self.keys, value) is None:
            raise ValidationError(f'Den Schlüssel {value} gibt es in keinem amtlichen Verzeichnis.')

    def formfield(self, **kwargs):
        if self.keys == TOWNS:  # found by searching, never chosen from all
            return super().formfield(**{'widget': TownInput, **kwargs})
        return super().formfield(**{'form_class': KeyChoice, 'keys': self.keys, 'town': self.town, **kwargs})


# Bringing editions in

def apply(listing, name, effective, entries, source='', successors=(), rename=True):
    """Bring a list to an edition of it: what is new comes in, what changed is changed, what it no longer has ends with
    the edition before (the last that had it), naming its successor where known. Returns the Edition."""
    last = Edition.objects.filter(listing=listing).order_by('-pk').first()  # the one brought in before
    known, given, successors = {k.code: k for k in Key.objects.filter(listing=listing)}, {e[0]: e[1:] for e in entries}, dict(successors)
    since = last.effective + timedelta(days=1) if last else None
    new = [Key(listing=listing, code=code, name=n, within=w, postcode=p, since=since) for code, (n, w, p) in given.items() if code not in known]
    changed = [k for code, k in known.items() if code in given and (k.until or rename and (k.name, k.within, k.postcode) != given[code])]
    for k in changed:
        k.until, k.successor = None, ''
        if rename:
            k.name, k.within, k.postcode = given[k.code]
    ended = [k for code, k in known.items() if code not in given and k.until is None]
    for k in ended:
        k.until, k.successor = last.effective if last else effective - timedelta(days=1), successors.get(k.code, '')
    Key.objects.bulk_create(new, batch_size=2000)
    Key.objects.bulk_update(changed + ended, ['name', 'within', 'postcode', 'until', 'successor'], batch_size=2000)
    names = {k.code: k.name for k in [*known.values(), *new]}
    notes = [] if last is None else [  # what anyone may ask about: the first edition only lays the list down
        *(f'Neu: {k.name} ({k.code})' for k in new),
        *(f'Ersetzt: {k.name} ({k.code}) durch {names.get(k.successor, k.successor)} ({k.successor})' if k.successor else f'Beendet: {k.name} ({k.code})' for k in ended),
        *(f'Geändert: {k.name} ({k.code})' for k in changed),
    ]
    return Edition.objects.create(
        listing=listing, name=name, effective=effective, source=source, added=len(new), renamed=len(changed), ended=len(ended),
        notes='\n'.join(notes[:40] + ([f'… und {len(notes) - 40} weitere'] if len(notes) > 40 else [])),
    )


def schluesselverzeichnis():
    """The lists of the Schlüsselverzeichnis shipped with this version (core.destatis), and the Kreise ended before it,
    each with the Kreis that took its place. Nothing if it is in already."""
    if Edition.objects.filter(name=destatis.EDITION).exists():
        return []
    lists = [
        (Key.List.LAND, [(c, n, '', '') for c, n in destatis.STATES]),
        (Key.List.KREIS, [(c, n, c[:2], '') for c, n in destatis.DISTRICTS]),
        (Key.List.STAAT, [(c, n, '', '') for c, n in destatis.COUNTRIES]),
        (Key.List.HOCHSCHULE, [(c, n, '', '') for c, n in destatis.UNIVERSITIES]),
        (Key.List.FACH, [(c, n, '', '') for c, n in destatis.FIELDS]),
        (Key.List.ABSCHLUSS, [(c, n, '', '') for c, n in destatis.DEGREES]),
    ]
    with transaction.atomic():
        Key.objects.bulk_create(  # before the first edition here, as the territorial changes have them
            Key(listing=Key.List.KREIS, code=code, name=name, within=code[:2], until=until, successor=successor) for code, name, until, successor in destatis.ENDED
        )
        return [apply(listing, destatis.EDITION, destatis.EFFECTIVE, entries, 'Statistisches Bundesamt') for listing, entries in lists]


M = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def read(path):
    """(Stand, {Gemeindeschlüssel: (name, postcode)}, {Kreisschlüssel: name}) of a Gemeindeverzeichnis as Destatis
    publishes it (GV-ISys, xlsx): records 60 are Gemeinden, 40 Kreise. Read with the standard library."""
    with zipfile.ZipFile(path) as book:
        shared = [''.join(t.text or '' for t in item.iter(M + 't')) for item in ElementTree.fromstring(book.read('xl/sharedStrings.xml'))]
        rows = ElementTree.fromstring(book.read('xl/worksheets/sheet2.xml')).find(M + 'sheetData')
    stand, towns, districts = None, {}, {}
    for row in rows:
        cells = {}
        for cell in row:
            value = cell.find(M + 'v')
            value = '' if value is None else value.text
            cells[re.sub(r'\d', '', cell.get('r'))] = shared[int(value)] if cell.get('t') == 's' else value
        if stand is None and (found := re.search(r'am (\d\d\.\d\d\.\d{4})', cells.get('A', ''))):
            stand = datetime.strptime(found[1], '%d.%m.%Y').date()
        if cells.get('A') == '60':
            towns[cells['C'] + cells['D'] + cells['E'] + cells['G']] = (cells['H'], cells.get('N', ''))
        elif cells.get('A') == '40':
            districts[cells['C'] + cells['D'] + cells['E']] = cells['H']
    return stand, towns, districts


def gemeindeverzeichnis(path, source=''):
    """Bring in an edition of the Gemeindeverzeichnis: Kreise new and gone, Gemeinden with their postcode. A Gemeinde gone
    under one key and new under another with the same name and postcode is the same town, as Hanau, kreisfrei since 2026:
    the new key replaces the old, and whoever lives there follows it to its Kreis. A Kreis gone is replaced by the one most
    of its towns went to. Returns the editions brought in; none if this one is in already."""
    stand, towns, districts = read(path)
    if Edition.objects.filter(listing=Key.List.GEMEINDE, effective=stand).exists():
        return []
    before = {k.code: k for k in Key.objects.filter(listing=Key.List.GEMEINDE, until=None)}
    fresh = {(n, p): code for code, (n, p) in towns.items() if code not in before}
    successors = {code: fresh[k.name, k.postcode] for code, k in before.items() if code not in towns and (k.name, k.postcode) in fresh}
    went = defaultdict(Counter)
    for old, new in successors.items():
        went[old[:5]][new[:5]] += 1
    name, source = f'Gemeindeverzeichnis, Stand {date_format(stand, "j.n.Y")}', source or f'Statistisches Bundesamt, {Path(path).name}'
    with transaction.atomic():
        editions = [
            apply(Key.List.KREIS, name, stand, [(c, n, c[:2], '') for c, n in districts.items()], source,
                  {k: c.most_common(1)[0][0] for k, c in went.items() if k not in districts}, rename=False),  # the Schlüsselverzeichnis names them
            apply(Key.List.GEMEINDE, name, stand, [(c, n, c[:5], p) for c, (n, p) in towns.items()], source, successors),
        ]
        if moved := {old: new for old, new in successors.items() if old[:5] != new[:5]}:
            editions[-1].notes += '\n' + follow(moved, stand)
            editions[-1].save(update_fields=['notes'])
    return editions


def follow(moved, stand):
    """Whoever lives in a town now in another Kreis moves with it: town and Kreis of the Heimat- and Semesterwohnsitz, each
    change kept with its reason. Where the HZB was gained stays as it was. Returns what happened, a line per town."""
    from . import history
    from .models import Person

    names, lines = dict(Key.objects.filter(listing=Key.List.KREIS).values_list('code', 'name')), []
    for old, new in moved.items():
        town, n = Key.objects.get(listing=Key.List.GEMEINDE, code=new), 0
        day = date_format(Key.objects.get(listing=Key.List.GEMEINDE, code=old).until + timedelta(days=1), 'j.n.Y')
        reason = f'Gebietsänderung zum {day}: {town.name} gehört zum Kreis {new[:5]} ({names[new[:5]]}), vorher {old[:5]} ({names[old[:5]]})'
        with history.because(reason):
            for person in Person.objects.filter(Q(home_town=old) | Q(residence_town=old)):
                for place, kept in (('home', 'home_town'), ('residence', 'residence_town')):
                    if getattr(person, kept) == old:
                        setattr(person, place, new[:5])
                        setattr(person, kept, new)
                person.save(update_fields=['home', 'home_town', 'residence', 'residence_town'])
                n += 1
        lines.append(f'{town.name}: jetzt {names[new[:5]]} ({new[:5]}) statt {names[old[:5]]} ({old[:5]}); {n} Wohnsitz{"e" if n != 1 else ""} umgestellt.')
    return '\n'.join(lines)


def review():
    """Who may have changed Kreis without moving: a Heimat- or Semesterwohnsitz in a Kreis that gave a town to another within
    the last year, without the town to tell. (people, {Kreis: [towns it gave]})."""
    from students.models import Study

    from .models import Person

    gave = defaultdict(list)
    for town in Key.objects.filter(listing=Key.List.GEMEINDE, until__gte=timezone.localdate() - timedelta(days=366)).exclude(successor=''):
        if town.successor[:5] != town.code[:5]:
            gave[town.code[:5]].append(town)
    people = Person.objects.filter(
        Q(home__in=gave, home_town='') | Q(residence__in=gave, residence_town=''),
        studies__status__in=[Study.Status.ENROLLED, Study.Status.ADMITTED, Study.Status.APPLIED],
    ).distinct().order_by('last_name', 'first_name')
    return people, gave


def bundled():
    """Every edition shipped with this version that is not in yet, oldest first. Returns the editions brought in."""
    found = schluesselverzeichnis()
    for path in sorted(DATA.glob('gemeindeverzeichnis-*.xlsx')):
        found += gemeindeverzeichnis(path)
    return found
