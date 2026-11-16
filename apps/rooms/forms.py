import uuid
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django import forms
from django.db import transaction
from django.db.models import Min, Prefetch, Q
from django.template.defaultfilters import floatformat
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.html import escape

from core import history
from core.dates import ClockField, DayField, starting
from core.apps import plural
from core.models import Holiday, Institution, Office, Person, Term
from core.shortcuts import ids
from core.text import collated
from .models import Booking, BookingCategory, Option, Poll, Room, later, span
from .plans import agenda


def bookable(person):
    """Rooms a person may book: all with the right to, where it holds; teachers and students the rooms of their Institute,
    except event and meeting rooms, which are given out centrally."""
    if person.everywhere('book_any_room'):
        return Room.objects.all()
    rooms = person.within(Room.objects.all(), 'book_any_room')
    if person.roles & {'teacher', 'student'}:
        rooms |= Room.objects.filter(institutes__in=person.institutes()).exclude(function__central=True)
    return rooms.distinct()


def categories(person):
    """What a person books: everything with the right to book any room, else what teachers or students book."""
    if 'book_any_room' in person.roles:
        return BookingCategory.objects.all()
    return BookingCategory.objects.filter(**{'teachers' if 'teacher' in person.roles else 'students': True})


def horizon(person):
    """How many hours ahead a person books: teachers as the Hochschule says, students as far as the furthest of their
    categories reaches; None for an office that books any room."""
    rules, roles = Institution.get(), person.roles
    if 'book_any_room' in roles:
        return None
    if 'teacher' in roles:
        return rules.teacher_ahead_days * 24
    return max((c.ahead_hours or rules.student_ahead_hours for c in categories(person)), default=0)


def quota(person, day, exclude=None):
    """Hours a student has booked on `day` that count against their daily hours, last-minute bookings excepted."""
    counted = person.bookings.filter(category__quota=True, **starting(day)).exclude(pk=exclude)
    last_minute = timedelta(hours=Institution.get().last_minute_hours)
    return round(sum((b.end - b.start).total_seconds() for b in counted if b.start - b.created > last_minute) / 3600, 1)


def free_again(room, start, length, exclude=None):
    """When `room` is next free for `length` from `start` on, that day; None if not before midnight."""
    midnight = timezone.make_aware(datetime.combine(timezone.localtime(start).date() + timedelta(days=1), time()))
    for other in Booking.objects.filter(room=room, category__blocks=True, end__gt=start, start__lt=midnight).exclude(pk=exclude).order_by('start'):
        if other.start >= start + length:
            break
        start = max(start, other.end)
    return start if start + length <= midnight else None


class Buildings(forms.models.ModelChoiceIterator):
    """Rooms grouped by building, those booked lately first; read only when the choices are shown."""

    def __iter__(self):
        if self.field.empty_label is not None:
            yield '', self.field.empty_label
        rooms, groups = list(self.queryset), defaultdict(list)
        if recent := [self.choice(room) for pk in self.field.recent for room in rooms if room.pk == pk]:
            yield 'Zuletzt gebucht', recent
        for room in rooms:
            groups['Zentral vergeben, auf Anfrage' if room.pk in self.field.asked else room.building].append(self.choice(room))
        yield from sorted(groups.items(), key=lambda group: group[0] == 'Zentral vergeben, auf Anfrage')


class RoomField(forms.ModelChoiceField):
    """Rooms by building, each with what it is for: a room number alone tells few people anything."""

    iterator = Buildings
    recent = asked = ()
    need = 0  # the seats a course needs: rooms with fewer say so

    def label_from_instance(self, room):
        small = 'zu klein' if room.capacity < (self.need or 0) else ''
        return ' · '.join(filter(None, [room.name, room.purpose if room.purpose != room.name else '', f'{room.capacity} Pl.', small]))


class Nearby(forms.models.ModelChoiceIterator):
    """Only those a booking is likely for, the ticked first: who plays with the person, their class, their ensembles.
    Anyone else enrolled is found by typing a name (students:suggest), never listed (docs/privacy.md)."""

    def __iter__(self):
        picked = {str(pk) for pk in self.field.picked}
        shown = self.queryset.filter(Q(pk__in=self.field.picked) | self.field.near).distinct()
        for person in sorted(shown, key=lambda p: (str(p.pk) not in picked, collated(p.name))):
            yield self.choice(person)


class People(forms.CheckboxSelectMultiple):
    template_name = 'core/people.html'


class Participants(forms.ModelMultipleChoiceField):
    iterator = Nearby
    picked, near = (), Q(pk__in=[])

    def label_from_instance(self, person):  # the Hauptfach tells namesakes apart without showing anyone's Matrikel
        return f'{person.name} · {person.subject}'


class BookingForm(forms.ModelForm):
    day = DayField(label='Tag')
    begin = ClockField(label='Von')
    until = ClockField(label='Bis', example='15:45')
    participants = Participants(
        label='Teilnehmende', queryset=None, required=False, widget=People,
        help_text='Sehen den Termin in ihrem Stundenplan. Weitere Studierende finden Sie, wenn Sie oben einen Namen tippen.',
    )
    repeat_until = DayField(label='Wöchentlich wiederholen bis', required=False, help_text='Die Serie wird nur gespeichert, wenn jeder Termin frei ist.')
    field_order = ['room', 'category', 'day', 'begin', 'until', 'title', 'description', 'programme', 'admission', 'audience', 'internal', 'ensemble', 'participants', 'repeat_until']
    layout = [(None, [
        'room', 'category', ('day', 'begin', 'until'), 'title', 'description', 'programme', ('admission', 'audience'), 'internal', 'ensemble', 'participants', 'repeat_until',
    ])]

    class Meta:
        model = Booking
        fields = ['room', 'category', 'title', 'description', 'programme', 'admission', 'audience', 'internal', 'ensemble', 'participants']
        field_classes = {'room': RoomField}
        widgets = {'description': forms.Textarea(attrs={'rows': 3}), 'programme': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            start, end = timezone.localtime(self.instance.start), timezone.localtime(self.instance.end)
            self.initial.update(day=start.date(), begin=start.time(), until=end.time())
        self.person, roles, self.began = person, person.roles, self.instance.start
        self.office = 'book_any_room' in roles
        self.student = not (self.office or 'teacher' in roles)
        self.fields['room'].empty_label = 'Bitte wählen'
        rooms = self.fields['room'].queryset = bookable(person)
        if 'teacher' in roles and not person.everywhere('book_any_room'):  # halls given out centrally: theirs to ask for
            asked = Room.objects.filter(function__central=True).exclude(pk__in=rooms.values('pk'))
            self.fields['room'].asked = set(asked.values_list('pk', flat=True))
            self.fields['room'].queryset = Room.objects.filter(Q(pk__in=rooms.values('pk')) | Q(pk__in=asked.values('pk')))
        lately = person.bookings.filter(room__in=rooms).order_by('-created').values_list('room', flat=True)[:30]
        self.fields['room'].recent = list(dict.fromkeys(lately))[:3]
        if not self.instance.pk and not self.initial.get('room'):  # practice is mostly in the same room
            self.initial['room'] = next(iter(self.fields['room'].recent), None)
        self.fields['category'].queryset = categories(person) | BookingCategory.objects.filter(key=self.instance.category_id)  # never silently changed
        self.fields['category'].empty_label = None
        groups = {}  # what a category is: teaching, an event, a service, a closure
        for c in self.fields['category'].queryset:
            groups.setdefault(c.get_tone_display(), []).append((c.key, c.name))
        if len(groups) > 1:
            self.fields['category'].choices = list(groups.items())
        public = ','.join(BookingCategory.objects.filter(public=True).values_list('key', flat=True))
        for name in ('programme', 'admission', 'audience', 'internal'):  # an event's details, shown for the categories the public sees
            self.fields[name].widget.attrs['data-for'] = f'category:{public}'
        self.fields['title'].required = False
        self.fields['title'].help_text = 'Leer: die Kategorie, z. B. „Übebuchung“.'
        from courses.models import Ensemble

        term = Term.current()  # the person's ensembles this term: as a member, or coached; their members take part
        ensembles = Ensemble.objects.filter(Q(members__person=person) | Q(coach=person), course__term=term).distinct().prefetch_related('members__person')
        if ensembles:
            self.fields['ensemble'].queryset = ensembles
            self.fields['ensemble'].label_from_instance = lambda e: f'{e.name}: {", ".join(s.person.last_name for s in e.members.all())}'
            self.fields['ensemble'].empty_label = 'Keines'
        else:
            del self.fields['ensemble']
        enrolled, field = Q(studies__status='enrolled'), self.fields['participants']
        field.queryset = Person.objects.filter(enrolled).annotate(subject=Min('studies__subject__name', filter=enrolled)).exclude(pk=person.pk)
        field.near = Q(studies__teacher=person) | Q(studies__ensembles__coach=person) if 'teacher' in roles else Q(pk__in=[]) if ensembles else Q(studies__ensembles__members__person=person)
        field.picked = ids(self.data.getlist('participants')) if self.is_bound else list(  # with an ensemble, only who joins it
            self.instance.participants.exclude(studies__ensembles=self.instance.ensemble_id).values_list('pk', flat=True) if self.instance.ensemble_id
            else self.instance.participants.values_list('pk', flat=True)
        ) if self.instance.pk else []
        if self.student:  # practice needs a room and a time; a title and who else comes only for the categories that ask for others
            for name in ('description', 'programme', 'admission', 'audience', 'internal'):
                del self.fields[name]
            together = list(BookingCategory.objects.filter(students=True, participants__gt=0))
            for name in ('title', 'participants', 'ensemble'):
                if name in self.fields:
                    self.fields[name].widget.attrs['data-for'] = f'category:{",".join(c.key for c in together)}'
            field.help_text += ''.join(f' Bei „{escape(c.name)}“ mindestens {plural(c.participants, "weitere Person", "weitere Personen")}.' for c in together)
            if 'ensemble' in self.fields:
                self.fields['participants'].help_text = 'Mit einem Ensemble nehmen seine Mitglieder teil; hier nur, wer dazukommt.'
        if self.student or self.instance.pk:
            del self.fields['repeat_until']

    def brief(self):
        """What booking beside the room plan asks (rooms/_quick.html): the time, the category, the ensemble; the title
        only of teachers and offices, a rehearsal taking its ensemble's."""
        return ['begin', 'until', 'category', 'ensemble'] + ([] if self.student else ['title'])

    def rest(self):
        """The fields booking beside the room plan does not ask, sent along as they are."""
        return [field for field in self if field.name not in self.brief()]

    def clean(self):
        data = super().clean()
        room, day, begin, until = data.get('room'), data.get('day'), data.get('begin'), data.get('until')
        if not (day and begin and until):
            return data
        if until <= begin:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        start = self.instance.start = timezone.make_aware(datetime.combine(day, begin))
        end = self.instance.end = timezone.make_aware(datetime.combine(day, until))
        self.instance.requested = bool(room and room.pk in self.fields['room'].asked)  # until the office confirms
        if self.instance.requested and data.get('repeat_until'):
            raise forms.ValidationError('Zentral vergebene Räume bitte je Termin anfragen.')
        if ensemble := data.get('ensemble'):  # its members take part, with whoever else is ticked
            data['participants'] = list({*(data.get('participants') or []), *(s.person for s in ensemble.members.all())} - {self.person})
            self.instance.course = ensemble.course
            data['title'] = data.get('title') or f'Probe {ensemble.name}'
        if not self.office and data.get('category'):
            self.check_rules(data['category'], start, end, data.get('participants') or [])
        if self.student:  # nobody is in two places at once: not in two rooms, not practising during their own lesson
            if busy := agenda(self.person).filter(start__lt=end, end__gt=start).exclude(pk=self.instance.pk).select_related('room').first():
                raise forms.ValidationError(f'Sie haben zu dieser Zeit schon {busy.label} in {busy.room} ({timezone.localtime(busy.start):%H:%M}–{timezone.localtime(busy.end):%H:%M}).')
        if not room:
            return data
        if data.get('audience') and room.capacity and data['audience'] > room.capacity:
            raise forms.ValidationError(f'{room} fasst {room.capacity} Personen, erwartet sind {data["audience"]}.')
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
        rooms = sorted(self.fields['room'].queryset.exclude(pk__in=taken), key=lambda r: (r.function_id != b.room.function_id, r.building != b.room.building, collated(r.name)))
        length = b.end - b.start
        start = free_again(b.room_id, b.start, length, exclude=b.pk)
        return {'rooms': rooms[:4], 'later': start and (start, start + length)}

    def check_rules(self, category, start, end, participants):
        """The Hochschule's booking rules (Institution) and those of the category."""
        now, rules = timezone.now(), Institution.get()
        if end < now or start < now - timedelta(minutes=15) and start != self.began:  # one under way may end earlier or later
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
        if not category.quota:
            return
        if start - now <= timedelta(hours=rules.last_minute_hours):  # last-minute: free of the quota, but not a whole day
            if end - start > timedelta(hours=rules.last_minute_hours):
                raise forms.ValidationError(f'Kurzfristig, ohne Kontingent, höchstens {plural(rules.last_minute_hours, "Stunde", "Stunden")} am Stück.')
            return
        used = quota(self.person, timezone.localtime(start).date(), exclude=self.instance.pk)
        if used + (end - start).total_seconds() / 3600 > rules.student_daily_hours:
            raise forms.ValidationError(
                f'Dafür reicht die Übezeit nicht: höchstens {plural(rules.student_daily_hours, "Stunde", "Stunden")} am Tag, gezählt sind schon {number_format(used)} Std. '
                f'Was in den nächsten {rules.last_minute_hours} Stunden beginnt, zählt nicht.'
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


class ServiceForm(forms.Form):
    """A service an event needs, booked with it in its room: tuning, technical support, transport, caretaking."""

    category = forms.ModelChoiceField(label='Dienst', queryset=BookingCategory.objects.filter(tone=BookingCategory.Tone.SERVICE), empty_label=None)
    begin = ClockField(label='Von')
    until = ClockField(label='Bis', example='15:45')

    def clean(self):
        data = super().clean()
        if data.get('begin') and data.get('until') and data['until'] <= data['begin']:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        return data


def asked(person, course=None):
    """Whom a poll may ask, as choices: those of its course first (commission or teachers, then who takes part), then the
    teachers, the person's Institut first, then the offices; each once."""
    from courses.forms import teachers
    from courses.models import Course

    groups, seen = [], set()

    def add(label, people):
        people = [(pk, name) for pk, name in people if pk not in seen]
        seen.update(pk for pk, _ in people)
        if people:
            groups.append((label, people))

    if course:
        add('Kommission' if course.kind in Course.PANEL else 'Lehrende', [(p.pk, str(p)) for p in course.staff.all()])
        add('Teilnehmende', [(p.pk, str(p)) for p in Person.objects.filter(studies__participations__course=course, studies__status='enrolled').distinct()])
    for label, people in teachers(course.subject if course else None, {person.institute_id}):
        add(label, people)
    for office in Office.objects.prefetch_related(Prefetch('user_set', Person.objects.filter(is_active=True).order_by('last_name'))):
        add(office.name, [(p.pk, str(p)) for p in office.user_set.all()])
    return groups


def months(count=6):
    """The first days of this month and the `count` - 1 after it."""
    today = timezone.localdate()
    return [date(today.year + (today.month - 1 + n) // 12, (today.month - 1 + n) % 12 + 1, 1) for n in range(count)]


class Calendar(forms.CheckboxSelectMultiple):
    """Days ticked on months laid out as calendars: weekends and public holidays set apart, days gone by not offered."""

    template_name = 'rooms/calendar.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        today, picked, shown = timezone.localdate(), set(context['widget']['value'] or ()), months()
        off = Holiday.days(shown[0], shown[-1] + timedelta(days=31), public=True)
        context['widget']['months'] = [(first, [None] * first.weekday() + [
            {'value': d.isoformat(), 'day': d, 'off': d.weekday() > 4 or d in off, 'holiday': d in off, 'past': d < today, 'on': d.isoformat() in picked}
            for d in (first + timedelta(days=n) for n in range(monthrange(first.year, first.month)[1]))
        ]) for first in shown]
        context['widget']['weekdays'] = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
        return context


TIMES = {'9-17': ('ganzer Tag', time(9), time(17)), '9-13': ('vormittags', time(9), time(13)), '13-17': ('nachmittags', time(13), time(17))}


class PollForm(forms.ModelForm):
    """A Terminumfrage: whom it asks, and the times it offers: each day ticked at each time chosen."""

    days = forms.TypedMultipleChoiceField(label='Tage', coerce=date.fromisoformat, widget=Calendar, error_messages={'required': 'Bitte die Tage anklicken.'})
    times = forms.MultipleChoiceField(
        label='Zeiten', required=False, initial=['9-17'], widget=forms.CheckboxSelectMultiple(attrs={'class': 'choices'}),
        choices=[(key, f'{label}, {a.hour}–{b.hour} Uhr') for key, (label, a, b) in TIMES.items()],
    )
    begin = ClockField(label='Andere Zeit von', example='10', required=False)
    until = ClockField(label='bis', example='12', required=False)
    by = DayField(label='Antwort bis')
    layout = [(None, [('title', 'category')]), ('Wer', ['people']), ('Wann', ['days', 'times', ('begin', 'until')]), ('Wo', ['room']), ('Antworten', ['by', 'open'])]

    class Meta:
        model = Poll
        fields = ['title', 'category', 'room', 'people', 'by', 'open']
        field_classes = {'room': RoomField}
        widgets = {'people': forms.CheckboxSelectMultiple(attrs={'class': 'quiet'})}
        help_texts = {'room': 'Leer: erst beim Wählen, oder ohne Raum.', 'open': 'Sonst sieht sie nur, wer fragt.'}

    def __init__(self, *args, person, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.person, self.course = person, course
        self.fields['category'].queryset = BookingCategory.objects.exclude(tone__in=[BookingCategory.Tone.SERVICE, BookingCategory.Tone.BLOCKED, BookingCategory.Tone.CANCELLED])
        self.fields['room'].queryset = bookable(person)
        self.fields['room'].empty_label = 'Noch keiner'
        self.fields['people'].choices = asked(person, course)
        self.fields['people'].help_text = 'Wer kommen kann, aber nicht muss, wird danach in der Umfrage „optional“.'
        self.fields['days'].choices = [(d.isoformat(), d) for first in months() for d in (first + timedelta(days=n) for n in range(monthrange(first.year, first.month)[1]))]
        if course:
            from courses.models import Course

            key = 'eignungspruefung' if course.kind == Course.Kind.AUDITION else 'pruefung' if course.kind in Course.PANEL else 'unterricht'
            self.initial.setdefault('title', course.title)
            self.initial.setdefault('category', key)
            for name in ('title', 'category'):  # the exam's or audition's own, as the page says above
                self.fields[name].widget = forms.HiddenInput()
            if course.kind == Course.Kind.AUDITION:  # how many days to offer
                from admissions.auditions import booked, need

                if (plan := need(course)) and (lacking := plan.hours - booked(course)) > 0:
                    self.fields['days'].help_text = (
                        f'Gebraucht: {floatformat(lacking, -1)} Stunden, {plan.count} × {plan.each} Min. ({plan.basis or "die Eingeladenen"}). '
                        'Mehr Tage anbieten, als gebraucht werden.'
                    )
                    if lacking <= 4:  # a morning will do
                        self.initial.setdefault('times', ['9-13'])
            self.initial.setdefault('people', list(course.staff.values_list('pk', flat=True)))
            self.initial.setdefault('room', course.bookings.values_list('room', flat=True).first())
        self.initial.setdefault('by', timezone.localdate() + timedelta(days=7))

    def clean(self):
        data = super().clean()
        if data.get('by') and data['by'] < timezone.localdate():
            self.add_error('by', 'Liegt in der Vergangenheit.')
        spans = [TIMES[key][1:] for key in data.get('times') or ()]
        begin, until = data.get('begin'), data.get('until')
        if begin and until and until <= begin or bool(begin) != bool(until):
            self.add_error('until', '„Bis“ muss nach „von“ liegen.' if begin and until else 'Bitte von und bis angeben.')
        elif begin:
            spans.append((begin, until))
        if not spans and not (begin or until):  # a wrong time of one's own says so itself
            self.add_error('times', 'Bitte eine Zeit wählen.')
        self.slots = [
            (timezone.make_aware(datetime.combine(day, a)), timezone.make_aware(datetime.combine(day, b)))
            for day in sorted(data.get('days') or ()) for a, b in sorted(spans)
        ]
        if len(self.slots) > 60:
            raise forms.ValidationError(f'{len(self.slots)} Zeiten sind zu viele zum Beantworten; höchstens 60.')
        return data

    def save(self):
        poll = super().save(commit=False)
        poll.person, poll.course = self.person, self.course
        with transaction.atomic():
            poll.save()
            self.save_m2m()
            Option.objects.bulk_create(Option(poll=poll, start=start, end=end) for start, end in self.slots)
        return poll


def overlapping(times):
    """Bookings that overlap any of `times` (each with start and end)."""
    found = Q(pk__in=[])
    for t in times:
        found |= Q(start__lt=t.end, end__gt=t.start)
    return found


class ChooseForm(forms.Form):
    """The room for the times chosen: of those the person books, the ones free at all of them."""

    room = RoomField(label='Raum', queryset=None, required=False, empty_label='Ohne Raum')

    def __init__(self, *args, person, poll, options, **kwargs):
        super().__init__(*args, **kwargs)
        taken = Booking.objects.filter(overlapping(options), category__blocks=True, requested=False).values('room')
        self.fields['room'].queryset = bookable(person).exclude(pk__in=taken)
        lately = Booking.objects.filter(category=poll.category_id).order_by('-created').values_list('room', flat=True)[:40]  # where such dates are held
        self.fields['room'].recent = list(dict.fromkeys(lately))[:5]
        free = set(self.fields['room'].queryset.filter(pk__in=[poll.room_id, *self.fields['room'].recent]).values_list('pk', flat=True))
        self.fields['room'].initial = next((pk for pk in [poll.room_id, *(self.fields['room'].recent if poll.course_id else [])] if pk in free), None)  # an exam is held in a room


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


class Choices(forms.CheckboxSelectMultiple):
    """Boxes to tick in a list that folds open, showing what is ticked."""

    template_name = 'core/choices.html'


class FindForm(forms.Form):
    """The room plan's filter: rooms by name; the rooms free from–until on the day it shows, for so many, with all the
    equipment ticked; „Von“ alone asks for an hour."""

    raum = forms.CharField(label='Raum', required=False, widget=forms.TextInput(attrs={'placeholder': 'z. B. 208', 'size': 10}))
    von = ClockField(label='Frei von', required=False)
    bis = ClockField(label='bis', required=False)
    personen = forms.IntegerField(label='Personen', min_value=1, required=False, widget=forms.TextInput(attrs={'inputmode': 'numeric', 'size': 4}))
    ausstattung = forms.MultipleChoiceField(label='Ausstattung', required=False, widget=Choices)

    def __init__(self, *args, rooms, **kwargs):  # the equipment offered: what the rooms one books have
        super().__init__(*args, **kwargs)
        items = {i.strip() for e in rooms.values_list('equipment', flat=True) for i in e.split(',')} - {''}
        self.fields['ausstattung'].choices = [(i, i) for i in sorted(items, key=collated)]

    def clean(self):
        data = super().clean()
        if data.get('von') and not data.get('bis'):
            data['bis'] = (datetime.combine(date.today(), data['von']) + timedelta(hours=1)).time()
        if data.get('von') and data['bis'] <= data['von']:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        return data
