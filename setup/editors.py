"""Every list staff keep up themselves, described once: which model, which fields, who may edit it."""
from dataclasses import dataclass, field
from typing import Callable

from django import forms
from django.contrib.auth.models import Permission
from django.db import models
from django.shortcuts import get_object_or_404

from core.access import CAPABILITIES, EVERYWHERE
from core.dates import DayField
from core.models import Holiday, Institute, Institution, Office, Person, Term
from courses.forms import AuditionForm
from courses.models import Area, Course, Format
from curriculum.forms import ModuleForm
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
    sections: list = None  # (heading, field names): a long form in parts; its fields follow from them
    form: type = forms.ModelForm
    columns: list = field(default_factory=list)  # (heading, attribute)
    search: list = field(default_factory=list)
    words: str = ''  # more words the search box finds it by
    queryset: Callable = None
    new: Callable = None  # request → the unsaved record a new form starts from
    deletable: bool = True  # people are deactivated, not deleted: their records hang on them
    single: bool = False  # one record only, like the Hochschule's own data
    invite: bool = False  # people: its form offers to send a sign-in invitation

    def __post_init__(self):
        if self.sections:
            self.fields = [name for _, names in self.sections for name in names]

    def objects(self, person):
        """The records the person keeps up: all, or those of the Institute and Studiengänge their office is limited to."""
        return person.within(self.queryset() if self.queryset else self.model.objects.all(), self.capability)

    def usable(self, person):
        """Whether the person keeps this list up: with the capability, and where it is limited, for a list whose records belong somewhere."""
        return self.capability in person.roles and (self.model.scope is not None or person.everywhere(self.capability))

    @property
    def creatable(self):
        return bool(self.section or self.new) and not (self.single and self.model.objects.exists())

    def start(self, request):
        return self.new(request) if self.new else self.model()


def formfield(model_field, **kwargs):
    """Date fields that understand '12.11.' and 'morgen' but ask for a full date, as records keep one; numbers with a
    decimal comma, in a text box (a number box drops the comma in some browsers); ticks instead of multiple selects."""
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
        fields = ['name', 'institutes', 'programs']
        labels = {'name': 'Name des Amts'}
        help_texts = {
            'name': 'Wie es an der Hochschule heißt, z. B. „Studierendensekretariat“.',
            'institutes': 'Leer lassen, wenn das Amt für die ganze Hochschule zuständig ist.',
            'programs': 'Ebenso. Mit Instituten und Studiengängen gilt jedes Recht für beides.',
        }
        widgets = {'institutes': forms.CheckboxSelectMultiple, 'programs': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['capabilities'] = list(self.instance.permissions.values_list('codename', flat=True))
            members = ', '.join(str(p) for p in self.instance.user_set.order_by('last_name'))
            self.note = f'Mitglieder: {members or "noch niemand"}. Wer dazugehört, wird bei den Konten eingetragen.'

    def clean(self):
        data = super().clean()
        if data.get('institutes') or data.get('programs'):
            labels = dict(choice for _, group in CAPABILITIES for choice in group)
            if wide := [labels[c] for c in data.get('capabilities', []) if c in EVERYWHERE]:
                raise forms.ValidationError(f'Nur für die ganze Hochschule zu vergeben: {"; ".join(wide)}.')
        return data

    def save(self, commit=True):
        office = super().save()
        office.permissions.set(Permission.objects.filter(content_type__model='office', codename__in=self.cleaned_data['capabilities']))
        return office


class AccountForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ['title', 'first_name', 'last_name', 'email', 'username', 'institute', 'employment', 'obligation', 'groups', 'is_active']
        localized_fields = ['obligation']
        labels = {'groups': 'Ämter', 'is_active': 'Aktiv', 'username': 'Benutzername'}
        help_texts = {
            'groups': 'Was jemand darf, ergibt sich aus den Rechten der Ämter.',
            'institute': 'Für Lehrende: das Institut, dessen Räume sie buchen.',
            'is_active': 'Ohne Haken ist keine Anmeldung möglich.',
            'username': 'Wie beim Hochschulkonto.',
        }
        widgets = {'groups': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['groups'].queryset = Office.objects.all()

    def clean_email(self):
        email = self.cleaned_data['email']
        if email and Person.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Diese Adresse hat schon ein anderes Konto.')
        return email

    def save(self, commit=True):
        person = super().save(commit=False)
        if not person.pk:
            person.set_unusable_password()  # signs in with the Hochschule's login
        person.save()
        self.save_m2m()
        return person


def new_module(request):
    program = get_object_or_404(Program, pk=request.GET.get('studiengang'))
    return Module(program=program, parent=program.modules.filter(pk=request.GET.get('unter') or None).first())


EDITORS = {e.key: e for e in [
    Editor(
        'hochschule', Institution, 'Hochschule', 'manage_institution', 'Verwaltung', single=True, deletable=False,
        sections=[
            ('Briefkopf und Website', ['name', 'short_name', 'address', 'place', 'email', 'website', 'imprint', 'privacy', 'accessibility']),
            ('Aufnahme und Immatrikulation', ['matriculation_start', 'accept_days', 'retention_months', 'signup', 'insurers']),
            ('Rückmeldung und Prüfungen', ['late_fee', 'payee', 'iban', 'bic', 'withdraw_weeks']),
            ('Raumbuchung', ['student_daily_hours', 'student_ahead_hours', 'last_minute_hours', 'teacher_ahead_days']),
            ('Amtliche Statistik', ['university', 'region', 'report_id']),
        ],
        columns=[('Name', 'name'), ('Ort', 'place')],
        words='hochschuldaten anschrift regeln buchungsregeln matrikelnummer fristen löschfristen datenschutz hochschulnummer statistik landesamt',
    ),
    Editor(
        'aemter', Office, 'Ämter und Rechte', 'manage_accounts', 'Verwaltung', form=OfficeForm,
        columns=[('Amt', 'name'), ('Zuständig für', 'remit'), ('Mitglieder', 'user_set')], search=['name'], words='rollen rechte gruppen berechtigungen institut',
        queryset=lambda: Office.objects.prefetch_related('user_set', 'institutes', 'programs'),
    ),
    Editor(
        'konten', Person, 'Konten', 'manage_accounts', 'Verwaltung', form=AccountForm,
        columns=[('Name', 'name'), ('E-Mail', 'email'), ('Ämter', 'groups'), ('Institut', 'institute')],
        search=['first_name', 'last_name', 'email', 'username'], words='mitarbeitende lehrende zugang benutzer einladung passwort', invite=True,
        queryset=lambda: (
            Person.objects.filter(models.Q(groups__isnull=False) | models.Q(institute__isnull=False) | models.Q(studies=None)).distinct()
            .select_related('institute').prefetch_related('groups')
        ),
        deletable=False,
    ),
    Editor(
        'semester', Term, 'Semester', 'manage_terms', 'Verwaltung',
        fields=['name', 'start', 'end', 'lectures_start', 'lectures_end', 'exams_start', 'reregistration_start', 'deadline', 'late_deadline', 'fee'],
        columns=[('Semester', 'name'), ('Beginn', 'start'), ('Ende', 'end'), ('Rückmeldefrist', 'deadline'), ('Beitrag', 'fee')],
        search=['name'], words='unterrichtszeit vorlesungszeit rückmeldefrist semesterbeitrag termine',
        queryset=lambda: Term.objects.order_by('-start'),
    ),
    Editor(
        'unterrichtsfrei', Holiday, 'Unterrichtsfreie Tage', 'manage_terms', 'Verwaltung', fields=['name', 'first', 'last'],
        columns=[('Bezeichnung', 'name'), ('Von', 'first'), ('Bis', 'last')], search=['name'], words='feiertage ferien vorlesungsfrei weihnachten semesterplanung',
        queryset=lambda: Holiday.objects.order_by('-first'),
    ),
    Editor('institute', Institute, 'Institute', 'manage_institution', 'Verwaltung', fields=['name'], columns=[('Institut', 'name')], search=['name']),
    Editor(
        'personen', Person, 'Stammdaten', 'manage_students', deletable=False,
        sections=[  # what changes most often first
            ('Kontakt', ['address', 'phone', 'email']),
            ('Person', ['title', 'first_name', 'given_names', 'last_name', 'birth_name', 'gender', 'birth_date', 'birth_place', 'nationality', 'second_nationality']),
            ('Krankenversicherung', ['insurer', 'insurance']),
            ('Hochschulzugang', ['entrance', 'entrance_date', 'entrance_region']),
            ('Bisheriges Studium', ['first_university', 'first_term', 'external_terms', 'degree_university', 'degree_exam', 'degree_field', 'degree_date', 'degree_grade', 'vocational', 'internship']),
            ('Amtliche Statistik', ['home', 'residence']),
        ],
    ),
    Editor(
        'studiengaenge', Program, 'Studiengänge', 'edit_curriculum', 'Studium und Lehre',
        fields=[
            'degree', 'name', 'version', 'semesters', 'subjects', 'postgraduate', 'attempts', 'rounding', 'mean_rounding', 'honours', 'thesis_weeks',
            'level', 'qualification', 'english', 'outcomes', 'exam', 'kind', 'field', 'exam_office',
        ],
        columns=[('Abschluss', 'degree'), ('Studiengang', 'name'), ('PO-Version', 'version'), ('Regelstudienzeit', 'semesters')],
        search=['degree', 'name'], words='prüfungsordnung po fpso diploma supplement abschlussarbeit bearbeitungszeit statistik abschlussprüfung studienfach',
    ),
    Editor('module', Module, 'Module', 'edit_curriculum', form=ModuleForm, new=new_module),
    Editor(
        'fachgebiete', Area, 'Fachgebiete', 'manage_courses', 'Studium und Lehre', fields=['name', 'order'],
        columns=[('Fachgebiet', 'name'), ('Reihenfolge', 'order')], search=['name'], words='vorlesungsverzeichnis bereiche gliederung',
    ),
    Editor(
        'lehrformen', Format, 'Lehrformen', 'manage_courses', 'Studium und Lehre', fields=['name', 'short', 'per_head', 'order'],
        columns=[('Lehrform', 'name'), ('Kürzel', 'short')], search=['name', 'short'], words='vorlesung seminar übung einzelunterricht veranstaltungsart',
    ),
    Editor(
        'hauptfaecher', Subject, 'Hauptfächer', 'edit_curriculum', 'Studium und Lehre', fields=['name', 'institute', 'english', 'field'],
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
        fields=['name', 'purpose', 'building', 'function', 'institutes', 'capacity', 'equipment'],
        columns=[('Raum', 'name'), ('Bezeichnung', 'purpose'), ('Gebäude', 'building'), ('Funktion', 'function'), ('Plätze', 'capacity')],
        search=['name', 'purpose', 'building', 'equipment'], words='ausstattung flügel gebäude',
        queryset=lambda: Room.objects.select_related('function'),
    ),
    Editor(
        'instrumente', Instrument, 'Leihinstrumente', 'manage_instruments', 'Räume', fields=['number', 'name', 'maker', 'institute', 'note', 'available'],
        columns=[('Instrument', 'name'), ('Inventarnummer', 'number'), ('Institut', 'institute'), ('Verleihbar', 'available')],
        search=['name', 'number', 'maker'], words='instrumentenpool inventar leihe', queryset=lambda: Instrument.objects.select_related('institute'),
    ),
    Editor(
        'unterlagen', DocumentType, 'Unterlagenarten', 'run_auditions', 'Aufnahme',
        fields=['name', 'condition', 'subjects', 'required', 'shared', 'jury', 'note', 'amount', 'file_types', 'max_mb', 'single', 'order'],
        columns=[('Unterlagen', 'name'), ('In der Checkliste', 'condition'), ('Pflicht', 'required')],
        search=['name'], words='checkliste bewerbung video zeugnis nachweis hochladen',
        queryset=lambda: DocumentType.objects.exclude(key__in=DocumentType.SYSTEM),  # Bescheide and theses are filed by the system
    ),
    Editor(
        'raumfunktionen', RoomFunction, 'Raumfunktionen', 'manage_rooms', 'Räume', fields=['name', 'central', 'order'],
        columns=[('Funktion', 'name'), ('Zentral vergeben', 'central')], search=['name'],
    ),
    Editor(
        'kategorien', BookingCategory, 'Buchungskategorien', 'manage_rooms', 'Räume',
        fields=['name', 'tone', 'students', 'teachers', 'quota', 'ahead_hours', 'participants', 'public', 'blocks', 'order'],
        columns=[('Kategorie', 'name'), ('Farbe', 'tone'), ('Studierende', 'students'), ('Lehrende', 'teachers'), ('Öffentlich', 'public')],
        search=['name'], words='buchungsregeln farben kontingent veranstaltungen',
    ),
]}
