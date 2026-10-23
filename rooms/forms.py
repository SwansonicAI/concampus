import uuid
from datetime import datetime, timedelta

from django import forms
from django.db.models import Min, Q
from django.utils import timezone
from django.utils.formats import number_format

from core import history
from core.dates import ClockField, DayField
from core.apps import plural
from core.models import Institution, Person
from .models import Booking, BookingCategory, Room, later, span


def bookable(person):
    """Rooms a person may book: all with the right to; teachers and students the rooms of their Institute, except event
    and meeting rooms, which are given out centrally."""
    if 'book_any_room' in person.roles:
        return Room.objects.all()
    if not person.roles & {'teacher', 'student'}:
        return Room.objects.none()
    return Room.objects.filter(institutes__in=person.institutes()).exclude(function__central=True).distinct()


def quota(person, day, exclude=None):
    """Hours a student has booked on `day` that count against their daily hours, last-minute bookings excepted."""
    counted = person.bookings.filter(category__quota=True, start__date=day).exclude(pk=exclude)
    last_minute = timedelta(hours=Institution.get().last_minute_hours)
    return round(sum((b.end - b.start).total_seconds() for b in counted if b.start - b.created > last_minute) / 3600, 1)


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
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            start, end = timezone.localtime(self.instance.start), timezone.localtime(self.instance.end)
            self.initial.update(day=start.date(), begin=start.time(), until=end.time())
        self.person, roles = person, person.roles
        self.office = 'book_any_room' in roles
        self.student = not (self.office or 'teacher' in roles)
        self.fields['room'].queryset = bookable(person)
        self.fields['room'].empty_label = 'Bitte wählen'
        allowed = BookingCategory.objects.all()
        if not self.office:
            allowed = allowed.filter(teachers=True) if 'teacher' in roles else allowed.filter(students=True)
        self.fields['category'].queryset = allowed
        self.fields['category'].empty_label = None
        self.fields['title'].required = False
        self.fields['title'].help_text = 'Leer: die Kategorie, z. B. „Übebuchung“.'
        enrolled = Q(studies__status='enrolled')
        self.fields['participants'].queryset = (
            Person.objects.filter(enrolled).annotate(subject=Min('studies__subject__name', filter=enrolled)).order_by('last_name', 'first_name').exclude(pk=person.pk)
        )
        if self.student:
            del self.fields['description']
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
        if not room:
            return data
        if until := data.get('repeat_until'):
            weeks = range(1, (until - timezone.localtime(start).date()).days // 7 + 1)
            self.repeats = [(later(start, n), later(end, n)) for n in weeks]
            taken = [s for s, e in self.repeats if Booking(room=room, start=s, end=e).clashes().exists()]
            if taken:
                raise forms.ValidationError('Serie nicht möglich, belegt am ' + ', '.join(f'{t:%d.%m.}' for t in taken) + '.')
        return data

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
