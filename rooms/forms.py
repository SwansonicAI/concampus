import uuid
from datetime import datetime, timedelta

from django import forms
from django.utils import timezone
from django.utils.formats import number_format

from core import history
from core.dates import ClockField, DayField
from core.models import Person
from .models import Booking, Room, later

# The HfM's booking rules (Buchungsinformationen in Asimut)
STUDENT_AHEAD = timedelta(hours=24)  # students book at most a day ahead,
STUDENT_DAILY = 4  # at most four hours per calendar day,
LAST_MINUTE = timedelta(hours=3)  # but whatever starts within three hours does not count;
CHAMBER_AHEAD = timedelta(days=10)  # chamber music rehearsals ten days ahead, with at least one more student
TEACHER_AHEAD = timedelta(days=183)  # teachers six months ahead, without a quota


def bookable(person):
    """Rooms a person may book: all with the right to; teachers and students the rooms of their Institute, except event
    and meeting rooms, which are given out centrally."""
    if 'book_any_room' in person.roles:
        return Room.objects.all()
    if not person.roles & {'teacher', 'student'}:
        return Room.objects.none()
    return Room.objects.filter(institutes__in=person.institutes()).exclude(function__in=Room.OFFICE_ONLY).distinct()


def quota(person, day, exclude=None):
    """Hours a student has booked on `day` that count against the four a day: their practice and chamber music,
    last-minute bookings excepted."""
    counted = person.bookings.filter(category__in=Booking.STUDENT, start__date=day).exclude(pk=exclude)
    return round(sum((b.end - b.start).total_seconds() for b in counted if b.start - b.created > LAST_MINUTE) / 3600, 1)


class Participants(forms.ModelMultipleChoiceField):
    def label_from_instance(self, person):
        return f'{person.name}{f" ({person.matriculation})" if person.matriculation else ""}'


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
        allowed = Booking.Category.values if self.office else Booking.TEACHER if 'teacher' in roles else Booking.STUDENT
        self.fields['category'].choices = [(value, label) for value, label in Booking.Category.choices if value in allowed]
        if not self.instance.pk:
            self.initial.setdefault('category', allowed[0])
        self.fields['title'].required = False
        self.fields['title'].help_text = 'Leer: die Kategorie, z. B. „Übebuchung“.'
        self.fields['participants'].queryset = Person.objects.filter(studies__status='enrolled').distinct().order_by('last_name', 'first_name').exclude(pk=person.pk)
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
        if not self.office:
            self.check_rules(data.get('category'), start, end, data.get('participants') or [])
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
        now = timezone.now()
        if start < now - timedelta(minutes=15):
            raise forms.ValidationError('Der Termin liegt in der Vergangenheit.')
        if not self.student:
            if start > now + TEACHER_AHEAD:
                raise forms.ValidationError('Lehrende buchen höchstens sechs Monate im Voraus.')
            return
        if category == Booking.Category.CHAMBER:
            if start > now + CHAMBER_AHEAD:
                raise forms.ValidationError('Kammermusikproben lassen sich höchstens 10 Tage im Voraus buchen.')
            if not participants:
                raise forms.ValidationError('Für eine Kammermusikprobe bitte mindestens eine weitere Person aus dem Ensemble eintragen.')
        elif start > now + STUDENT_AHEAD:
            raise forms.ValidationError('Studierende buchen höchstens 24 Stunden im Voraus. Kammermusikproben gehen bis 10 Tage.')
        if start - now <= LAST_MINUTE:
            return  # last-minute: free of the quota
        used = quota(self.person, timezone.localtime(start).date(), exclude=self.instance.pk)
        if used + (end - start).total_seconds() / 3600 > STUDENT_DAILY:
            raise forms.ValidationError(
                f'Ihr Kontingent von {STUDENT_DAILY} Stunden am Tag reicht dafür nicht (gebucht: {number_format(used)} Std.). '
                'Was in den nächsten 3 Stunden beginnt, geht als Last-Minute-Buchung ohne Kontingent.'
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
