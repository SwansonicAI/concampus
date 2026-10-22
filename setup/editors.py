"""Every list staff keep up themselves, described once: which model, which fields, who may edit it."""
from dataclasses import dataclass, field
from typing import Callable

from django import forms
from django.contrib.auth.models import Permission
from django.db import models
from django.shortcuts import get_object_or_404

from core.access import CAPABILITIES
from core.dates import DayField
from core.models import Institute, Office, Person, Term
from courses.forms import AuditionForm
from courses.models import Course
from curriculum.forms import ModuleForm
from curriculum.models import Module, Program, Subject
from rooms.models import Room
from students.forms import StudyForm
from students.models import Study


@dataclass
class Editor:
    key: str  # in the URL
    model: type
    title: str
    capability: str
    section: str = ''  # where the start page of the settings lists it; empty: opened from elsewhere only
    fields: list = None
    form: type = forms.ModelForm
    columns: list = field(default_factory=list)  # (heading, attribute)
    search: list = field(default_factory=list)
    words: str = ''  # more words the search box finds it by
    queryset: Callable = None
    new: Callable = None  # request → the unsaved record a new form starts from
    deletable: bool = True  # people are deactivated, not deleted: their records hang on them

    def objects(self):
        return self.queryset() if self.queryset else self.model.objects.all()

    @property
    def creatable(self):
        return bool(self.section or self.new)

    def start(self, request):
        return self.new(request) if self.new else self.model()


def formfield(model_field, **kwargs):
    """Date fields that understand 'morgen' and '12.11.'; ticks instead of multiple selects."""
    if type(model_field) is models.DateField:
        return model_field.formfield(form_class=DayField, **kwargs)
    if isinstance(model_field, models.ManyToManyField):
        kwargs.setdefault('widget', forms.CheckboxSelectMultiple)
    return model_field.formfield(**kwargs)


class OfficeForm(forms.ModelForm):
    capabilities = forms.MultipleChoiceField(label='Rechte', choices=CAPABILITIES, widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = Office
        fields = ['name']
        labels = {'name': 'Name des Amts'}
        help_texts = {'name': 'Wie es an der Hochschule heißt, z. B. „Studierendensekretariat“.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['capabilities'] = list(self.instance.permissions.values_list('codename', flat=True))
            members = ', '.join(str(p) for p in self.instance.user_set.order_by('last_name'))
            self.note = f'Mitglieder: {members or "noch niemand"}. Wer dazugehört, wird bei den Konten eingetragen.'

    def save(self, commit=True):
        office = super().save()
        office.permissions.set(Permission.objects.filter(content_type__model='office', codename__in=self.cleaned_data['capabilities']))
        return office


class AccountForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ['title', 'first_name', 'last_name', 'email', 'username', 'institute', 'groups', 'is_active']
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
        'aemter', Office, 'Ämter und Rechte', 'manage_accounts', 'Verwaltung', form=OfficeForm,
        columns=[('Amt', 'name'), ('Mitglieder', 'user_set')], search=['name'], words='rollen rechte gruppen berechtigungen',
    ),
    Editor(
        'konten', Person, 'Konten', 'manage_accounts', 'Verwaltung', form=AccountForm,
        columns=[('Name', 'name'), ('E-Mail', 'email'), ('Ämter', 'groups'), ('Institut', 'institute')],
        search=['first_name', 'last_name', 'email', 'username'], words='mitarbeitende lehrende zugang benutzer',
        queryset=lambda: Person.objects.filter(models.Q(groups__isnull=False) | models.Q(institute__isnull=False) | models.Q(studies=None)).distinct(),
        deletable=False,
    ),
    Editor(
        'semester', Term, 'Semester', 'manage_terms', 'Verwaltung',
        fields=['name', 'start', 'end', 'lectures_start', 'lectures_end', 'reregistration_start', 'deadline', 'fee'],
        columns=[('Semester', 'name'), ('Beginn', 'start'), ('Ende', 'end'), ('Rückmeldefrist', 'deadline'), ('Beitrag', 'fee')],
        search=['name'], words='vorlesungszeit rückmeldefrist semesterbeitrag termine',
        queryset=lambda: Term.objects.order_by('-start'),
    ),
    Editor('institute', Institute, 'Institute', 'manage_institution', 'Verwaltung', fields=['name'], columns=[('Institut', 'name')], search=['name']),
    Editor(
        'personen', Person, 'Stammdaten', 'manage_students', deletable=False,
        fields=[
            'title', 'first_name', 'given_names', 'last_name', 'birth_name', 'gender', 'birth_date', 'birth_place', 'nationality',
            'second_nationality', 'address', 'email', 'entrance', 'entrance_date', 'entrance_place', 'external_terms', 'insurer', 'insurance',
        ],
    ),
    Editor(
        'studiengaenge', Program, 'Studiengänge', 'edit_curriculum', 'Studium und Lehre',
        fields=['degree', 'name', 'version', 'semesters', 'subjects'],
        columns=[('Abschluss', 'degree'), ('Studiengang', 'name'), ('PO-Version', 'version'), ('Regelstudienzeit', 'semesters')],
        search=['degree', 'name'], words='prüfungsordnung po fpso',
    ),
    Editor('module', Module, 'Module', 'edit_curriculum', form=ModuleForm, new=new_module),
    Editor('hauptfaecher', Subject, 'Hauptfächer', 'edit_curriculum', 'Studium und Lehre', fields=['name', 'institute'], columns=[('Hauptfach', 'name'), ('Institut', 'institute')], search=['name']),
    Editor('studien', Study, 'Studium', 'manage_students', form=StudyForm, deletable=False),
    Editor(
        'eignungspruefungen', Course, 'Eignungsprüfungen', 'run_auditions', 'Aufnahme', form=AuditionForm,
        columns=[('Prüfung', 'title'), ('Semester', 'term'), ('Studiengang', 'program'), ('Bewerbungsschluss', 'deadline'), ('Plätze', 'capacity')],
        search=['title', 'subject__name'], words='aufnahmeprüfung videostufe kommission bewerbungsfrist',
        queryset=lambda: Course.objects.filter(kind=Course.Kind.AUDITION).order_by('-term__start', 'title'),
        new=lambda request: Course(kind=Course.Kind.AUDITION, term=Term.upcoming() or Term.current()),
    ),
    Editor(
        'raeume', Room, 'Räume', 'manage_rooms', 'Räume',
        fields=['name', 'purpose', 'building', 'function', 'institutes', 'capacity', 'equipment'],
        columns=[('Raum', 'name'), ('Bezeichnung', 'purpose'), ('Gebäude', 'building'), ('Funktion', 'function'), ('Plätze', 'capacity')],
        search=['name', 'purpose', 'building', 'equipment'], words='ausstattung flügel gebäude',
    ),
]}
