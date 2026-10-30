"""Every list staff keep up themselves, described once: which model, which fields, who may edit it."""
from dataclasses import dataclass, field
from typing import Callable

from django import forms
from django.contrib.auth.models import Permission
from django.db import models
from django.shortcuts import get_object_or_404

from core.access import CAPABILITIES, EVERYWHERE
from core.dates import DayField, PastField
from core.models import Grant, Holiday, Institute, Institution, Membership, Office, Person, Term
from courses.forms import AuditionForm
from courses.models import Appointment, Area, Course, Format, Reduction
from curriculum.forms import ModuleForm, ProgramForm
from curriculum.models import Module, Program, Subject
from instruments.models import Instrument
from rooms.models import BookingCategory, Room, RoomFunction
from students.forms import StudyForm
from students.models import DocumentType, Study


@dataclass
class Editor:
    key: str  # in the URL
    model: type
    title: str
    capability: str
    section: str = ''  # where the start page of the settings lists it; empty: opened from elsewhere only
    fields: list = None
    sections: list = None  # the form's layout (core.templatetags.layout): (heading or None, rows of field names); its fields follow from them
    form: type = forms.ModelForm
    columns: list = field(default_factory=list)  # (heading, attribute)
    search: list = field(default_factory=list)
    words: str = ''  # more words the search box finds it by
    queryset: Callable = None
    new: Callable = None  # request → the unsaved record a new form starts from
    deletable: bool = True  # people are deactivated, not deleted: their records hang on them
    single: bool = False  # one record only, like the Hochschule's own data
    adding: bool = True  # False: only some fields of records kept up elsewhere, like the exam dates of a term
    invite: bool = False  # people: its form offers to send a sign-in invitation

    def __post_init__(self):
        if self.sections:
            self.fields = [name for _, rows in self.sections for row in rows for name in (row if isinstance(row, tuple) else (row,))]

    def objects(self, person):
        """The records the person keeps up: all, or those of the Institute and Studiengänge their office is limited to."""
        return person.within(self.queryset() if self.queryset else self.model.objects.all(), self.capability)

    def usable(self, person):
        """Whether the person keeps this list up: with the capability, and where it is limited, for a list whose records belong somewhere."""
        return self.capability in person.roles and (self.model.scope is not None or person.everywhere(self.capability))

    @property
    def creatable(self):
        return self.adding and bool(self.section or self.new) and not (self.single and self.model.objects.exists())

    def start(self, request):
        return self.new(request) if self.new else self.model()


PAST = {'birth_date', 'entrance_date', 'degree_date'}  # days that have been: digits only, never in the future


def formfield(model_field, **kwargs):
    """Date fields that understand '12.11.' and 'morgen' but ask for a full date, as records keep one; numbers with a
    decimal comma, in a text box (a number box drops the comma in some browsers); ticks instead of multiple selects."""
    if type(model_field) is models.DateField and model_field.name in PAST:
        return model_field.formfield(form_class=PastField, **kwargs)
    if type(model_field) is models.DateField:
        field = model_field.formfield(form_class=DayField, **kwargs)
        field.widget.attrs['placeholder'] = 'TT.MM.JJJJ'
        return field
    if isinstance(model_field, models.ManyToManyField):
        kwargs.setdefault('widget', forms.CheckboxSelectMultiple)
    if isinstance(model_field, models.DecimalField):
        kwargs['localize'] = True
    return model_field.formfield(**kwargs)


class OfficeForm(forms.ModelForm):
    capabilities = forms.MultipleChoiceField(label='Rechte', choices=CAPABILITIES, widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = Office
        fields = ['name', 'email', 'institutes', 'programs']
        labels = {'name': 'Name des Amts'}
        help_texts = {
            'name': 'Wie es an der Hochschule heißt, z. B. „Studierendensekretariat“.',
            'institutes': 'Leer lassen, wenn das Amt für die ganze Hochschule zuständig ist.',
            'programs': 'Ebenso. Mit Instituten und Studiengängen gilt jedes Recht für beides.',
        }
        widgets = {'institutes': forms.CheckboxSelectMultiple, 'programs': forms.CheckboxSelectMultiple}

    layout = [(None, [('name', 'email'), 'institutes', 'programs', 'capabilities'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['capabilities'].help_text = 'Neue Rechte gelten, sobald eine zweite Person sie unter „Freigaben“ bestätigt.'
        if self.instance.pk:
            self.initial['capabilities'] = list(self.instance.permissions.values_list('codename', flat=True))
            members = ', '.join(str(p) for p in self.instance.user_set.order_by('last_name'))
            waiting = '; '.join(', '.join(g.labels) for g in self.instance.grants.all())
            self.note = f'Mitglieder: {members or "noch niemand"}. Wer dazugehört, wird bei den Konten eingetragen.' + (f' Wartet auf Bestätigung: {waiting}.' if waiting else '')

    def clean(self):
        data = super().clean()
        if data.get('institutes') or data.get('programs'):
            labels = dict(choice for _, group in CAPABILITIES for choice in group)
            if wide := [labels[c] for c in data.get('capabilities', []) if c in EVERYWHERE]:
                raise forms.ValidationError(f'Nur für die ganze Hochschule zu vergeben: {"; ".join(wide)}.')
        return data

    def save(self, commit=True):
        """Rights taken away go at once; rights given wait for a second person to confirm them (Grant)."""
        office = super().save()
        chosen, held = set(self.cleaned_data['capabilities']), set(office.permissions.values_list('codename', flat=True))
        office.permissions.set(Permission.objects.filter(content_type__model='office', codename__in=chosen & held))
        if added := sorted(chosen - held):
            Grant.objects.create(office=office, rights=added, by=self.by)
        self.pending = bool(added)
        return office


class AccountForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ['title', 'first_name', 'last_name', 'email', 'username', 'institute', 'employment', 'obligation', 'groups', 'is_active']
        localized_fields = ['obligation']
        labels = {'groups': 'Ämter', 'is_active': 'Aktiv', 'username': 'Benutzername'}
        help_texts = {
            'groups': 'Was jemand darf, ergibt sich aus den Rechten der Ämter. Ein neues Amt gilt, sobald eine zweite Person es unter „Freigaben“ bestätigt; befristet unter „Vertretungen und Amtszeiten“.',
            'institute': 'Für Lehrende: das Institut, dessen Räume sie buchen.',
            'is_active': 'Ohne Haken ist keine Anmeldung möglich.',
            'username': 'Wie beim Hochschulkonto.',
        }
        widgets = {'groups': forms.CheckboxSelectMultiple}

    layout = [(None, [('title', 'first_name', 'last_name'), ('email', 'username'), ('institute', 'employment', 'obligation'), 'groups', 'is_active'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['groups'].queryset = Office.objects.all()
        if self.instance.pk and (waiting := Membership.objects.filter(person=self.instance, confirmed_by=None).select_related('office')):
            self.note = 'Wartet auf Bestätigung: ' + ', '.join(f'{m.office}' + (f' bis {m.until:%d.%m.%Y}' if m.until else '') for m in waiting) + '.'

    def clean_email(self):
        email = self.cleaned_data['email']
        if email and Person.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Diese Adresse hat schon ein anderes Konto.')
        return email

    def save(self, commit=True):
        """Leaving an office takes effect at once; joining one waits for a second person to confirm it (Membership)."""
        person = super().save(commit=False)
        if not person.pk:
            person.set_unusable_password()  # signs in with the Hochschule's login
        person.save()
        chosen, held = set(self.cleaned_data['groups']), set(Office.objects.filter(user=person))
        self.cleaned_data['groups'] = list(chosen & held)
        self.save_m2m()
        for office in chosen - held:
            Membership.objects.get_or_create(person=person, office=office, until=None, confirmed_by=None, defaults={'by': self.by})
        self.pending = bool(chosen - held)
        return person


class AppointmentForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ['person', 'year', 'subjects', 'general']
        widgets = {'subjects': forms.CheckboxSelectMultiple}

    layout = [(None, [('person', 'year'), 'subjects', 'general'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].queryset = Person.teachers().order_by('last_name', 'first_name')


class MembershipForm(forms.ModelForm):
    """An office held until a day, as a stand-in or for a term of office; it takes effect once a second person confirms it."""

    class Meta:
        model = Membership
        fields = ['person', 'office', 'until', 'note']

    layout = [(None, [('person', 'office'), ('until', 'note')])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['until'].required = True
        self.fields['person'].queryset = Person.objects.filter(models.Q(groups__isnull=False) | models.Q(institute__isnull=False)).distinct().order_by('last_name')

    def save(self, commit=True):
        self.instance.by, self.pending = self.instance.by or self.by, self.instance.confirmed_by is None
        return super().save(commit)


class ReductionForm(forms.ModelForm):
    class Meta:
        model = Reduction
        fields = ['person', 'term', 'reason', 'hours', 'note']
        localized_fields = ['hours']

    layout = [(None, [('person', 'term'), ('reason', 'hours'), 'note'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].queryset = Person.teachers().exclude(employment=Person.Employment.LEHRAUFTRAG).order_by('last_name', 'first_name')
        self.fields['term'].queryset = Term.objects.order_by('-start')

    def clean(self):
        """At most what ThürLVVO § 8 allows the function, of the teacher's Lehrverpflichtung."""
        data = super().clean()
        person, reason, hours = data.get('person'), data.get('reason'), data.get('hours')
        share = {'vizepraesidium': 75, 'dekanat': 50, 'prodekanat': 25, 'studiendekanat': 50, 'studiengangsleitung': 70}.get(reason)
        if person and hours is not None and share and person.obligation is not None and hours > person.obligation * share / 100:
            raise forms.ValidationError(f'Höchstens {share} % der Lehrverpflichtung von {person.obligation} LVS.')
        if reason == Reduction.Reason.TASK and hours is not None and hours > 2:
            raise forms.ValidationError('Für weitere Aufgaben höchstens 2 LVS.')
        return data


def new_module(request):
    program = get_object_or_404(Program, pk=request.GET.get('studiengang'))
    return Module(program=program, parent=program.modules.filter(pk=request.GET.get('unter') or None).first())


EDITORS = {e.key: e for e in [
    Editor(
        'hochschule', Institution, 'Hochschule', 'manage_institution', 'Verwaltung', single=True, deletable=False,
        sections=[
            ('Briefkopf und Website', [('name', 'short_name'), 'address', ('place', 'email', 'website'), ('imprint', 'privacy', 'accessibility')]),
            ('Aufnahme und Immatrikulation', [('matriculation_start', 'student_mail', 'accept_days'), 'signup', 'insurers']),
            ('Aufbewahrung', [('retention_months', 'file_years', 'core_years')]),
            ('Rückmeldung und Prüfungen', [('late_fee', 'withdraw_weeks'), 'payee', ('iban', 'bic')]),
            ('Raumbuchung', [('student_daily_hours', 'student_ahead_hours'), ('last_minute_hours', 'teacher_ahead_days')]),
            ('Deputat', ['load_since']),
            ('Amtliche Statistik', [('university', 'region', 'report_id')]),
        ],
        columns=[('Name', 'name'), ('Ort', 'place')],
        words='hochschuldaten anschrift regeln buchungsregeln matrikelnummer fristen löschfristen datenschutz hochschulnummer statistik landesamt',
    ),
    Editor(
        'aemter', Office, 'Ämter und Rechte', 'manage_accounts', 'Verwaltung', form=OfficeForm,
        columns=[('Amt', 'name'), ('E-Mail', 'email'), ('Zuständig für', 'remit'), ('Mitglieder', 'user_set')], search=['name'], words='rollen rechte gruppen berechtigungen institut',
        queryset=lambda: Office.objects.prefetch_related('user_set', 'institutes', 'programs'),
    ),
    Editor(
        'konten', Person, 'Konten', 'manage_accounts', 'Verwaltung', form=AccountForm,
        columns=[('Name', 'name'), ('E-Mail', 'email'), ('Ämter', 'groups'), ('Institut', 'institute'), ('Zuletzt angemeldet', 'last_login'), ('Aktiv', 'is_active')],
        search=['first_name', 'last_name', 'email', 'username'], words='mitarbeitende lehrende zugang benutzer einladung passwort', invite=True,
        queryset=lambda: (
            Person.objects.filter(models.Q(groups__isnull=False) | models.Q(institute__isnull=False) | models.Q(studies=None)).distinct()
            .select_related('institute').prefetch_related('groups')
        ),
        deletable=False,
    ),
    Editor(
        'mitgliedschaften', Membership, 'Vertretungen und Amtszeiten', 'manage_accounts', 'Verwaltung', form=MembershipForm,
        columns=[('Person', 'person'), ('Amt', 'office'), ('Bis', 'until'), ('Anlass', 'note'), ('Bestätigt von', 'confirmed_by')],
        search=['person__last_name', 'office__name', 'note'], words='vertretung befristet amtszeit stellvertretung urlaub elternzeit',
        queryset=lambda: Membership.objects.exclude(until=None).select_related('person', 'office', 'confirmed_by'),
    ),
    Editor(
        'semester', Term, 'Semester', 'manage_terms', 'Verwaltung',
        sections=[
            (None, ['name', ('start', 'end'), ('lectures_start', 'lectures_end'), ('reregistration_start', 'deadline', 'late_deadline')]),
            ('Semesterbeitrag', [('fee', 'fee_union', 'fee_ticket', 'fee_council')]),
            ('Planung der Lehre', [('planning', 'ready_by'), ('course_signup_start', 'course_signup_end')]),
        ],
        columns=[('Semester', 'name'), ('Beginn', 'start'), ('Ende', 'end'), ('Rückmeldefrist', 'deadline'), ('Beitrag', 'fee')],
        search=['name'], words='unterrichtszeit vorlesungszeit rückmeldefrist semesterbeitrag termine',
        queryset=lambda: Term.objects.order_by('-start'),
    ),
    Editor(
        'pruefungszeitraeume', Term, 'Prüfungszeiträume', 'manage_exams', 'Studium und Lehre',
        sections=[(None, [('exam_signup_start', 'exam_signup_end'), ('exams_start', 'exams_end')])],
        columns=[('Semester', 'name'), ('Anmeldung ab', 'exam_signup_start'), ('bis', 'exam_signup_end'), ('Prüfungen ab', 'exams_start'), ('bis', 'exams_end')],
        search=['name'], words='prüfungsanmeldung prüfungszeitraum prüfungswochen fristen', queryset=lambda: Term.objects.order_by('-start'),
        adding=False, deletable=False,
    ),
    Editor(
        'unterrichtsfrei', Holiday, 'Unterrichtsfreie Tage', 'manage_terms', 'Verwaltung', sections=[(None, ['name', ('first', 'last'), 'public'])],
        columns=[('Bezeichnung', 'name'), ('Von', 'first'), ('Bis', 'last'), ('Feiertag', 'public')], search=['name'], words='feiertage ferien vorlesungsfrei weihnachten semesterplanung',
        queryset=lambda: Holiday.objects.order_by('-first'),
    ),
    Editor('institute', Institute, 'Institute', 'manage_institution', 'Verwaltung', fields=['name'], columns=[('Institut', 'name')], search=['name']),
    Editor(
        'personen', Person, 'Stammdaten', 'manage_students', deletable=False,
        sections=[  # what changes most often first
            ('Kontakt', ['address', ('phone', 'email')]),
            ('Person', [('title', 'first_name', 'given_names'), ('last_name', 'birth_name', 'gender'), ('birth_date', 'birth_place'), ('nationality', 'second_nationality')]),
            ('Krankenversicherung', [('insurer', 'insurance')]),
            ('Hochschulzugang', [('entrance', 'entrance_date'), 'entrance_region', 'entrance_town']),
            ('Bisheriges Studium', [
                'first_university', ('first_term', 'external_terms'), 'degree_university', ('degree_exam', 'degree_field'), ('degree_date', 'degree_grade'), ('vocational', 'internship'),
            ]),
            ('Amtliche Statistik', ['home', 'home_town', 'residence', 'residence_town']),
        ],
    ),
    Editor(
        'studiengaenge', Program, 'Studiengänge', 'edit_curriculum', 'Studium und Lehre', form=ProgramForm,
        sections=[
            (None, [('degree', 'name', 'version'), ('semesters', 'attempts', 'thesis_weeks'), 'postgraduate', 'subjects']),
            ('Noten', [('rounding', 'mean_rounding', 'honours')]),
            ('Diploma Supplement und Statistik', [('level', 'kind', 'field'), ('qualification', 'english'), 'outcomes', ('exam', 'exam_office')]),
        ],
        columns=[('Abschluss', 'degree'), ('Studiengang', 'name'), ('PO-Version', 'version'), ('Regelstudienzeit', 'semesters')],
        search=['degree', 'name'], words='prüfungsordnung po fpso diploma supplement abschlussarbeit bearbeitungszeit statistik abschlussprüfung studienfach',
    ),
    Editor('module', Module, 'Module', 'edit_curriculum', form=ModuleForm, new=new_module),
    Editor(
        'pruefende', Appointment, 'Bestellte Prüfende', 'appoint_examiners', 'Studium und Lehre', form=AppointmentForm,
        columns=[('Prüfer:in', 'person'), ('Studienjahr', 'season'), ('Hauptfächer', 'subjects'), ('Ohne Hauptfach', 'general')],
        search=['person__last_name', 'person__first_name'], words='prüfer prüferliste prüfungsausschuss bestellung kommission eignungsprüfung',
        queryset=lambda: Appointment.objects.select_related('person').prefetch_related('subjects').order_by('-year', 'person__last_name'),
        new=lambda request: Appointment(year=Course(term=Term.current()).year if Term.current() else None),
    ),
    Editor(
        'ermaessigungen', Reduction, 'Ermäßigungen der Lehrverpflichtung', 'manage_teaching_load', 'Studium und Lehre', form=ReductionForm,
        columns=[('Lehrperson', 'person'), ('Semester', 'term'), ('Grund', 'reason'), ('LVS', 'hours'), ('Notiz', 'note')],
        search=['person__last_name', 'person__first_name', 'note'], words='deputat lvs dekanat studiendekanat entlastung lehrverpflichtungsverordnung',
        queryset=lambda: Reduction.objects.select_related('person', 'term'),
        new=lambda request: Reduction(term=Term.current(), person_id=request.GET.get('person') or None),
    ),
    Editor(
        'fachgebiete', Area, 'Fachgebiete', 'manage_courses', 'Studium und Lehre', sections=[(None, [('name', 'order')])],
        columns=[('Fachgebiet', 'name'), ('Reihenfolge', 'order')], search=['name'], words='vorlesungsverzeichnis bereiche gliederung',
    ),
    Editor(
        'lehrformen', Format, 'Lehrformen', 'manage_courses', 'Studium und Lehre', sections=[(None, [('name', 'short', 'order'), 'per_head'])],
        columns=[('Lehrform', 'name'), ('Kürzel', 'short')], search=['name', 'short'], words='vorlesung seminar übung einzelunterricht veranstaltungsart',
    ),
    Editor(
        'hauptfaecher', Subject, 'Hauptfächer', 'edit_curriculum', 'Studium und Lehre', sections=[(None, [('name', 'english'), ('institute', 'field')])],
        columns=[('Hauptfach', 'name'), ('Institut', 'institute')], search=['name'], queryset=lambda: Subject.objects.select_related('institute'),
    ),
    Editor('studien', Study, 'Studium', 'manage_students', form=StudyForm, deletable=False),
    Editor(
        'eignungspruefungen', Course, 'Eignungsprüfungen', 'run_auditions', 'Aufnahme', form=AuditionForm,
        columns=[('Prüfung', 'title'), ('Semester', 'term'), ('Studiengang', 'program'), ('Bewerbungsschluss', 'deadline'), ('Plätze', 'capacity')],
        search=['title', 'subject__name'], words='aufnahmeprüfung videostufe kommission bewerbungsfrist',
        queryset=lambda: Course.objects.filter(kind=Course.Kind.AUDITION).select_related('term', 'program').order_by('-term__start', 'title'),
        new=lambda request: Course(kind=Course.Kind.AUDITION, term=Term.upcoming() or Term.current()),
    ),
    Editor(
        'raeume', Room, 'Räume', 'manage_rooms', 'Räume',
        sections=[(None, [('name', 'purpose'), ('building', 'function', 'capacity'), 'equipment', 'institutes'])],
        columns=[('Raum', 'name'), ('Bezeichnung', 'purpose'), ('Gebäude', 'building'), ('Funktion', 'function'), ('Plätze', 'capacity')],
        search=['name', 'purpose', 'building', 'equipment'], words='ausstattung flügel gebäude',
        queryset=lambda: Room.objects.select_related('function'),
    ),
    Editor(
        'instrumente', Instrument, 'Leihinstrumente', 'manage_instruments', 'Räume', sections=[(None, [('name', 'maker', 'number'), 'institute', 'note', 'available'])],
        columns=[('Instrument', 'name'), ('Inventarnummer', 'number'), ('Institut', 'institute'), ('Verleihbar', 'available')],
        search=['name', 'number', 'maker'], words='instrumentenpool inventar leihe', queryset=lambda: Instrument.objects.select_related('institute'),
    ),
    Editor(
        'unterlagen', DocumentType, 'Unterlagenarten', 'run_auditions', 'Aufnahme',
        sections=[(None, [
            ('name', 'condition'), ('required', 'single', 'shared'), ('jury', 'health'), 'note', 'template', ('file_types', 'max_mb', 'max_files'), ('amount', 'order'), 'subjects',
        ])],
        columns=[('Unterlagen', 'name'), ('In der Checkliste', 'condition'), ('Pflicht', 'required')],
        search=['name'], words='checkliste bewerbung video zeugnis nachweis hochladen',
        queryset=lambda: DocumentType.objects.exclude(key__in=DocumentType.SYSTEM),  # Bescheide and theses are filed by the system
    ),
    Editor(
        'raumfunktionen', RoomFunction, 'Raumfunktionen', 'manage_rooms', 'Räume', sections=[(None, [('name', 'order'), 'central'])],
        columns=[('Funktion', 'name'), ('Zentral vergeben', 'central')], search=['name'],
    ),
    Editor(
        'kategorien', BookingCategory, 'Buchungskategorien', 'manage_rooms', 'Räume',
        sections=[(None, [('name', 'tone', 'order'), ('students', 'teachers', 'quota'), ('public', 'blocks'), ('ahead_hours', 'participants')])],
        columns=[('Kategorie', 'name'), ('Farbe', 'tone'), ('Studierende', 'students'), ('Lehrende', 'teachers'), ('Öffentlich', 'public')],
        search=['name'], words='buchungsregeln farben kontingent veranstaltungen',
    ),
]}
