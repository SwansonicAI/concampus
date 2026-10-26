import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta

from django import forms
from django.db.models import Exists, Min, OuterRef, Q
from django.utils import timezone
from django.utils.formats import number_format

from core import history
from core.dates import ClockField, DayField
from core.apps import plural
from core.models import Institution, Person
from .models import Booking, BookingCategory, Room, later, span


def bookable(person):
    """Rooms a person may book: all with the right to, where it holds; teachers and students the rooms of their Institute,
    except event and meeting rooms, which are given out centrally."""
    if person.everywhere('book_any_room'):
        return Room.objects.all()
    rooms = person.within(Room.objects.all(), 'book_any_room')
    if person.roles & {'teacher', 'student'}:
        rooms |= Room.objects.filter(institutes__in=person.institutes()).exclude(function__central=True)
    return rooms.distinct()


def quota(person, day, exclude=None):
    """Hours a student has booked on `day` that count against their daily hours, last-minute bookings excepted."""
    counted = person.bookings.filter(category__quota=True, start__date=day).exclude(pk=exclude)
    last_minute = timedelta(hours=Institution.get().last_minute_hours)
    return round(sum((b.end - b.start).total_seconds() for b in counted if b.start - b.created > last_minute) / 3600, 1)


class Buildings(forms.models.ModelChoiceIterator):
    """Rooms grouped by building, those booked lately first; read only when the choices are shown."""

    def __iter__(self):
        if self.field.empty_label is not None:
            yield '', self.field.empty_label
        rooms, groups = list(self.queryset), defaultdict(list)
        if recent := [self.choice(room) for pk in self.field.recent for room in rooms if room.pk == pk]:
            yield 'Zuletzt gebucht', recent
        for room in rooms:
            groups[room.building].append(self.choice(room))
        yield from groups.items()


class RoomField(forms.ModelChoiceField):
    """Rooms by building, each with what it is for: a room number alone tells few people anything."""

    iterator = Buildings
    recent = ()

    def label_from_instance(self, room):
        return f'{room.name} · {room.purpose}' if room.purpose not in ('', room.name) else room.name


class Participants(forms.ModelMultipleChoiceField):
    def label_from_instance(self, person):  # the Hauptfach tells namesakes apart without showing anyone's Matrikel
        return f'{person.name} · {person.subject}'


class BookingForm(forms.ModelForm):
    day = DayField(label='Tag')
    begin = ClockField(label='Von')
    until = ClockField(label='Bis')
    participants = Participants(
        label='Teilnehmende', queryset=None, required=False, widget=forms.CheckboxSelectMultiple,
        help_text='Sehen den Termin in ihrem Stundenplan. Bei einer Kammermusikprobe mindestens eine weitere Person.',
    )
    repeat_until = DayField(label='Wöchentlich wiederholen bis', required=False, help_text='Die Serie wird nur gespeichert, wenn jeder Termin frei ist.')
    field_order = ['room', 'category', 'day', 'begin', 'until', 'title', 'description', 'participants', 'repeat_until']

    class Meta:
        model = Booking
        fields = ['room', 'category', 'title', 'description', 'participants']
        field_classes = {'room': RoomField}
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            start, end = timezone.localtime(self.instance.start), timezone.localtime(self.instance.end)
            self.initial.update(day=start.date(), begin=start.time(), until=end.time())
        self.person, roles = person, person.roles
        self.office = 'book_any_room' in roles
        self.student = not (self.office or 'teacher' in roles)
        self.fields['room'].empty_label = 'Bitte wählen'
        rooms = self.fields['room'].queryset = bookable(person)
        lately = person.bookings.filter(room__in=rooms).order_by('-created').values_list('room', flat=True)[:30]
        self.fields['room'].recent = list(dict.fromkeys(lately))[:3]
        if not self.instance.pk and not self.initial.get('room'):  # practice is mostly in the same room
            self.initial['room'] = next(iter(self.fields['room'].recent), None)
        allowed = BookingCategory.objects.all()
        if not self.office:
            allowed = allowed.filter(teachers=True) if 'teacher' in roles else allowed.filter(students=True)
        self.fields['category'].queryset = allowed | BookingCategory.objects.filter(key=self.instance.category_id)  # never silently changed
        self.fields['category'].empty_label = None
        self.fields['title'].required = False
        self.fields['title'].help_text = 'Leer: die Kategorie, z. B. „Übebuchung“.'
        enrolled = Q(studies__status='enrolled')
        mates = Person.objects.filter(studies__ensembles__members__person=person, pk=OuterRef('pk'))  # who plays with them comes first
        self.fields['participants'].queryset = (
            Person.objects.filter(enrolled).annotate(subject=Min('studies__subject__name', filter=enrolled), mate=Exists(mates))
            .order_by('-mate', 'last_name', 'first_name').exclude(pk=person.pk)
        )
        if self.student:  # practice needs a room and a time; a title and who else comes only for the categories that ask for others
            del self.fields['description']
            together = ','.join(BookingCategory.objects.filter(students=True, participants__gt=0).values_list('key', flat=True))
            for name in ('title', 'participants'):
                self.fields[name].widget.attrs['data-for'] = f'category:{together}'
        if self.student or self.instance.pk:
            del self.fields['repeat_until']

    def clean(self):
        data = super().clean()
        room, day, begin, until = data.get('room'), data.get('day'), data.get('begin'), data.get('until')
        if not (day and begin and until):
            return data
        if until <= begin:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        start = self.instance.start = timezone.make_aware(datetime.combine(day, begin))
        end = self.instance.end = timezone.make_aware(datetime.combine(day, until))
        if not self.office and data.get('category'):
            self.check_rules(data['category'], start, end, data.get('participants') or [])
        if self.student:  # nobody is in two places at once: not in two rooms, not practising during their own lesson
            from .views import agenda

            if busy := agenda(self.person).filter(start__lt=end, end__gt=start).exclude(pk=self.instance.pk).select_related('room').first():
                raise forms.ValidationError(f'Sie haben zu dieser Zeit schon {busy.label} in {busy.room} ({timezone.localtime(busy.start):%H:%M}–{timezone.localtime(busy.end):%H:%M}).')
        if not room:
            return data
        if until := data.get('repeat_until'):
            weeks = range(1, (until - timezone.localtime(start).date()).days // 7 + 1)
            self.repeats = [(later(start, n), later(end, n)) for n in weeks]
            taken = [s for s, e in self.repeats if Booking(room=room, start=s, end=e).clashes().exists()]
            if taken:
                raise forms.ValidationError('Serie nicht möglich, belegt am ' + ', '.join(f'{t:%d.%m.}' for t in taken) + '.')
        return data

    def instead(self):
        """Where the room is taken: the rooms free then, like ones first, and when this one is free again that day for as
        long. None while nothing is in the way."""
        b = self.instance
        if not (self.errors and b.room_id and b.start and b.end and b.clashes().exists()):
            return None
        taken = Booking.objects.filter(start__lt=b.end, end__gt=b.start, category__blocks=True).values('room')
        rooms = sorted(self.fields['room'].queryset.exclude(pk__in=taken), key=lambda r: (r.function_id != b.room.function_id, r.building != b.room.building, r.name))
        midnight = timezone.make_aware(datetime.combine(timezone.localtime(b.start).date() + timedelta(days=1), time()))
        start, length = b.start, b.end - b.start
        for other in Booking.objects.filter(room=b.room_id, category__blocks=True, end__gt=b.start, start__lt=midnight).exclude(pk=b.pk).order_by('start'):
            if other.start >= start + length:
                break
            start = max(start, other.end)
        return {'rooms': rooms[:4], 'later': (start, start + length) if start + length <= midnight else None}

    def check_rules(self, category, start, end, participants):
        """The Hochschule's booking rules (Institution) and those of the category."""
        now, rules = timezone.now(), Institution.get()
        if start < now - timedelta(minutes=15):
            raise forms.ValidationError('Der Termin liegt in der Vergangenheit.')
        if not self.student:
            if start > now + timedelta(days=rules.teacher_ahead_days):
                raise forms.ValidationError(f'Lehrende buchen höchstens {rules.teacher_ahead_days} Tage im Voraus.')
            return
        ahead = category.ahead_hours or rules.student_ahead_hours
        if start > now + timedelta(hours=ahead):
            raise forms.ValidationError(f'„{category}“ buchen Studierende höchstens {span(ahead)} im Voraus.')
        if len(participants) < category.participants:
            raise forms.ValidationError(f'Für „{category}“ bitte mindestens {plural(category.participants, "weitere Person", "weitere Personen")} eintragen.')
        if not category.quota or start - now <= timedelta(hours=rules.last_minute_hours):
            return  # last-minute: free of the quota
        used = quota(self.person, timezone.localtime(start).date(), exclude=self.instance.pk)
        if used + (end - start).total_seconds() / 3600 > rules.student_daily_hours:
            raise forms.ValidationError(
                f'Ihr Kontingent von {rules.student_daily_hours} Stunden am Tag reicht dafür nicht (gebucht: {number_format(used)} Std.). '
                f'Was in den nächsten {rules.last_minute_hours} Stunden beginnt, zählt nicht zum Kontingent.'
            )

    def save(self):
        booking = super().save(commit=False)
        if not booking.pk:
            booking.person = self.person
        repeats = getattr(self, 'repeats', [])
        if repeats:
            booking.series = uuid.uuid4()
        booking.save()
        self.save_m2m()
        copies = Booking.objects.bulk_create(
            Booking(room=booking.room, start=s, end=e, person=booking.person, category=booking.category, title=booking.title,
                    description=booking.description, series=booking.series)
            for s, e in repeats
        )
        history.saved(copies)
        for copy in copies:
            copy.participants.set(booking.participants.all())
        return booking


class CancelForm(forms.Form):
    """Calling off one date, with its make-up date if there is one and a word to those taking part."""

    note = forms.CharField(label='Nachricht', required=False, widget=forms.TextInput(attrs={'placeholder': 'freiwillig, z. B. wegen Krankheit'}))
    day = DayField(label='Nachholtermin', required=False)
    begin = ClockField(label='Von', required=False)
    until = ClockField(label='Bis', required=False)
    room = RoomField(label='Raum', queryset=None, required=False, empty_label='derselbe Raum')

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = bookable(person)

    def clean(self):
        data = super().clean()
        given = [data.get(name) for name in ('day', 'begin', 'until')]
        if any(given) and not all(given):
            raise forms.ValidationError('Für den Nachholtermin bitte Tag, Von und Bis angeben.')
        if all(given) and data['until'] <= data['begin']:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        return data


class FindForm(forms.Form):
    day = DayField(label='Tag')
    start = ClockField(label='Von')
    end = ClockField(label='Bis')
    seats = forms.IntegerField(label='Personen', min_value=1, required=False)
    equipment = forms.CharField(label='Ausstattung', required=False, widget=forms.TextInput(attrs={'placeholder': 'z. B. Flügel, Orgel'}))

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['end'] <= data['start']:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        return data
