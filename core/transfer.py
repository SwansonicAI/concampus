"""Data in and out of Campus as one JSON document (docs/transfer.md): Semester, Hauptfächer, Studiengänge with their
Studienplan, and people with their studies, semesters and results. Records are named by what names them outside, a
Matrikelnummer, a Semester's name, a Studiengang's Abschluss, name and PO-Version, a module's Kürzel or its place in the
plan, never by database ids, so that a former system maps onto it. Loading updates what is there and adds what is not;
it removes nothing, and loads all or nothing."""
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from courses.models import Course, Participation
from curriculum.models import Module, Program, Subject
from students.models import Study, StudyTerm
from . import history
from .models import Institute, Person, Term

FORMAT = 'campus/1'
SKIPPED = {'id', 'password', 'last_login', 'is_superuser', 'is_staff', 'date_joined'}  # never travel


def own(model):
    """The fields of `model` a document carries as they are; references are named apart."""
    return [f for f in model._meta.concrete_fields if not f.is_relation and f.name not in SKIPPED]


def compact(found):
    return {k: v for k, v in found.items() if v not in (None, '', [])}


def record(obj, **named):
    """A record's own fields and its `named` references, leaving out what is empty."""
    return compact({f.name: f.value_from_object(obj) for f in own(type(obj))} | named)


def program_key(program):
    return [program.degree, program.name, program.version]


def module_keys(program):
    """{module pk: name}: its Kürzel, else its titles from the top of the plan down, joined by ' › '."""
    modules = {m.pk: m for m in program.modules.all()}

    def path(m):
        return f'{path(modules[m.parent_id])} › {m.title}' if m.parent_id else m.title

    return {pk: m.code or path(m) for pk, m in modules.items()}


def plan(program):
    keys, modules = module_keys(program), list(program.modules.order_by('pk').prefetch_related('waived'))

    def below(parent):
        return [
            record(m, waived=[s.name for s in m.waived.all()], modules=below(m.pk))
            for m in modules if m.parent_id == parent
        ]

    return record(program, subjects=sorted(s.name for s in program.subjects.all()), modules=below(None)), keys


def dump(people=None, programs=None):
    """The document for these people and Studiengänge, by default all, with every Semester and Hauptfach."""
    programs = Program.objects.all() if programs is None else programs
    keys = {}
    plans = []
    for p in programs.prefetch_related('subjects'):
        found, keys[p.pk] = plan(p)
        plans.append(found)
    people = (Person.objects.all() if people is None else people).select_related('institute').order_by('matriculation', 'username')
    studies = Study.objects.filter(person__in=people).select_related('program', 'subject', 'start_term', 'teacher').order_by('start_term__start', 'program__degree', 'program__name', 'subject__name')
    for program in {s.program for s in studies} - set(programs):
        keys[program.pk] = module_keys(program)
    results = Participation.objects.filter(study__in=studies, module__isnull=False, passed__isnull=False).select_related('course__term', 'module')
    by_study = {}
    for r in results:
        on = timezone.localdate(r.told or r.course.released or r.created)
        by_study.setdefault(r.study_id, []).append(compact({
            'module': keys[r.module.program_id][r.module_id], 'term': r.course.term.name, 'on': on,
            'grade': r.grade, 'passed': r.passed, 'remark': r.remark, 'note': r.note,
        }))
    terms = {}
    for t in StudyTerm.objects.filter(study__in=studies).select_related('term').order_by('term__start'):
        terms.setdefault(t.study_id, []).append(record(t, term=t.term.name))
    per_person = {}
    for s in studies:
        key = keys[s.program_id]
        per_person.setdefault(s.person_id, []).append(record(
            s, program=program_key(s.program), subject=s.subject.name, start_term=s.start_term.name, teacher=s.teacher and s.teacher.username,
            focus=key.get(s.focus_id), advised=key.get(s.advised_id), terms=terms.get(s.pk, []),
            results=sorted(by_study.get(s.pk, []), key=lambda r: (r['on'], r['module'])),
        ))
    return {
        'format': FORMAT,
        'terms': [record(t) for t in Term.objects.all()],
        'institutes': [record(i) for i in Institute.objects.all()],
        'subjects': [record(s, institute=s.institute and s.institute.name) for s in Subject.objects.select_related('institute')],
        'programs': plans,
        'people': [record(p, institute=p.institute and p.institute.name, studies=per_person.get(p.pk, [])) for p in people],
    }


class Loader:
    """One document loaded: each record checked as Campus checks what is typed in, its faults gathered with where they
    are, nothing kept unless all fit."""

    def __init__(self, document):
        self.document, self.faults, self.counts = document, [], {}

    def keep(self, obj, where, **found):
        """Check and save `obj` with its own fields from `found`; False if it does not fit."""
        for f in own(type(obj)):
            setattr(obj, f.name, f.to_python(found[f.name]) if f.name in found else f.get_default())
        try:
            obj.full_clean(exclude=['password'])
        except ValidationError as error:
            self.faults.append(f'{where}: {"; ".join(f"{k}: {" ".join(v)}" for k, v in error.message_dict.items())}')
            return False
        obj.save()
        self.counts[type(obj)._meta.verbose_name_plural] = self.counts.get(type(obj)._meta.verbose_name_plural, 0) + 1
        return True

    def named(self, model, where, **key):
        """The record `key` names, or a fault."""
        if found := model.objects.filter(**key).first():
            return found
        self.faults.append(f'{where}: {model._meta.verbose_name} {", ".join(map(str, key.values()))} gibt es nicht')
        return None

    def run(self):
        if self.document.get('format') != FORMAT:
            raise ValueError(f'Kein Dokument im Format {FORMAT}.')
        with transaction.atomic(), history.quietly():
            for i, t in enumerate(self.document.get('terms', [])):
                self.keep(Term.objects.filter(name=t.get('name')).first() or Term(), f'terms[{i}]', **t)
            for i, s in enumerate(self.document.get('institutes', [])):
                self.keep(Institute.objects.filter(name=s.get('name')).first() or Institute(), f'institutes[{i}]', **s)
            for i, s in enumerate(self.document.get('subjects', [])):
                subject = Subject.objects.filter(name=s.get('name')).first() or Subject()
                subject.institute = s.get('institute') and self.named(Institute, f'subjects[{i}]', name=s['institute'])
                self.keep(subject, f'subjects[{i}]', **s)
            for i, p in enumerate(self.document.get('programs', [])):
                self.program(p, f'programs[{i}]')
            people = self.document.get('people', [])
            found = [self.person(p, f'people[{i}]') for i, p in enumerate(people)]
            for i, (person, p) in enumerate(zip(found, people)):  # once everyone is in, as a teacher may come later
                for j, s in enumerate(p.get('studies', []) if person else []):
                    self.study(person, s, f'people[{i}].studies[{j}]')
            if self.faults:
                raise ValueError('\n'.join(self.faults))
        return self.counts

    def program(self, p, where):
        program = Program.objects.filter(degree=p.get('degree'), name=p.get('name'), version=p.get('version')).first() or Program()
        if not self.keep(program, where, **p):
            return
        program.subjects.set([s for name in p.get('subjects', []) if (s := self.named(Subject, where, name=name))])
        keys = {key: pk for pk, key in module_keys(program).items()}

        def place(modules, parent, path, where):
            for i, m in enumerate(modules):
                here, key = f'{where}.modules[{i}]', m.get('code') or (f'{path} › {m.get("title")}' if path else m.get('title'))
                module = Module.objects.filter(pk=keys.get(key)).first() or Module(program=program)
                module.parent = parent
                if self.keep(module, here, **m):
                    module.waived.set([s for name in m.get('waived', []) if (s := self.named(Subject, here, name=name))])
                    place(m.get('modules', []), module, f'{path} › {module.title}' if path else module.title, here)

        place(p.get('modules', []), None, '', where)

    def person(self, p, where):
        key = {'matriculation': p['matriculation']} if p.get('matriculation') else {'username': p.get('username')}
        person = Person.objects.filter(**key).first() or Person()
        if person.pk is None:
            person.set_unusable_password()  # signs in through the Hochschule's login, or sets a password by e-mail
        person.institute = p.get('institute') and self.named(Institute, where, name=p['institute'])
        return person if self.keep(person, where, **({'username': p.get('matriculation')} | p)) else None

    def study(self, person, s, where):
        program = self.named(Program, where, degree=s['program'][0], name=s['program'][1], version=s['program'][2])
        subject, start = self.named(Subject, where, name=s.get('subject')), self.named(Term, where, name=s.get('start_term'))
        if not (program and subject and start):
            return
        modules = {key: pk for pk, key in module_keys(program).items()}
        study = Study.objects.filter(person=person, program=program, subject=subject, start_term=start).first() or Study(person=person, program=program, subject=subject, start_term=start)
        study.teacher = s.get('teacher') and self.named(Person, where, username=s['teacher'])
        study.focus_id, study.advised_id = modules.get(s.get('focus')), modules.get(s.get('advised'))
        if not self.keep(study, where, **s):
            return
        for i, t in enumerate(s.get('terms', [])):
            if term := self.named(Term, f'{where}.terms[{i}]', name=t.get('term')):
                self.keep(StudyTerm.objects.filter(study=study, term=term).first() or StudyTerm(study=study, term=term), f'{where}.terms[{i}]', **t)
        for i, r in enumerate(s.get('results', [])):
            self.result(study, modules, r, f'{where}.results[{i}]')

    def result(self, study, modules, r, where):
        """A result as an exam of its semester, released and told on its day; a second in the same semester as another."""
        term, module = self.named(Term, where, name=r.get('term')), Module.objects.filter(pk=modules.get(r.get('module'))).first()
        try:
            on = timezone.make_aware(datetime.combine(models.DateField().to_python(r.get('on')), time(12)))
        except (TypeError, ValidationError):
            on = self.faults.append(f'{where}: on: kein Datum')
        if module is None:
            self.faults.append(f'{where}: Modul {r.get("module")} gibt es im Studiengang nicht')
        if not (term and module and on):
            return
        found = Participation.objects.filter(study=study, module=module, course__term=term, course__released=on).first()
        if found is None:
            taken = Participation.objects.filter(study=study, module=module, course__term=term).count()
            title = module.title if not taken else f'{module.title} ({taken + 1}. Termin)'
            course, _ = Course.objects.get_or_create(term=term, kind=Course.Kind.EXAM, title=title, released=on)
            course.modules.add(module)
            found = Participation(course=course, study=study, module=module, created=on)
        if self.keep(found, where, **r | {'created': on}):
            found.told, found.told_result = on, found.result
            found.save(update_fields=['told', 'told_result'])


def load(document):
    """Load a document; returns {what: how many}, or raises ValueError naming each record that does not fit."""
    return Loader(document).run()
