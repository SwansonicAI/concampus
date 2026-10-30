import re
import uuid
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from django.db import models
from django.utils.formats import number_format
from django.urls import reverse
from django.utils import timezone

from core import destatis, texts
from core.models import Institution, Keyed, Message, Person, Term, Tracked
from core.text import matching
from curriculum.models import Program, Subject, weighted

EXITS = [  # grounds of Exmatrikulation as Immatrikulationsordnungen word them, keyed as the official statistics count them (destatis.EXITS)
    ('1', 'Abschluss des Studiums'), ('4', 'Auf Antrag: Wechsel an eine andere Hochschule'), ('6', 'Auf Antrag: Abbruch des Studiums'),
    ('2', 'Auf Antrag: Unterbrechung des Studiums'), ('5', 'Auf Antrag: Freiwilligendienst'), ('9', 'Auf Antrag: anderer Grund'),
    ('7', 'Keine Rückmeldung, Krankenversicherung oder Beiträge nicht nachgewiesen'), ('8', 'Prüfung endgültig nicht bestanden'),
    ('0', 'Von Amts wegen: anderer Grund'),
]
OFFICIAL = {'0', '7', '8'}  # grounds the Hochschule acts on by itself: its Bescheid can be challenged
LEAVES = [  # grounds of Beurlaubung, likewise (destatis.LEAVES)
    ('1', 'Erkrankung'), ('7', 'Mutterschutz, Elternzeit oder Pflege von Angehörigen'), ('4', 'Studienbedingter Auslandsaufenthalt'),
    ('3', 'Praktische Tätigkeit'), ('2', 'Vorbereitung auf eine Abschlussprüfung'), ('5', 'Freiwilligendienst'),
    ('9', 'Anderer wichtiger Grund, z. B. Mitarbeit in Gremien'),
]
LEAVE_TERMS = 2  # Urlaubssemester a student may take in the rule; more only through the office
FIRST_TERM_LEAVES = {'1', '7'}  # the grounds that allow leave in the first Fachsemester


class Study(Tracked, models.Model):
    """One person's path through one programme, from application to graduation."""

    owner = 'person_id'
    capabilities = ('manage_students',)
    scope = (['subject__institute'], ['program'])
    settled = ('status', 'since', 'exit_reason')  # Zulassung, Immatrikulation, Exmatrikulation: each with its Bescheid

    class Status(models.TextChoices):
        APPLIED = 'applied', 'Beworben'
        ADMITTED = 'admitted', 'Zugelassen'
        WAITLISTED = 'waitlisted', 'Nachrückliste'
        REJECTED = 'rejected', 'Abgelehnt'
        ENROLLED = 'enrolled', 'Immatrikuliert'
        GUEST = 'guest', 'Gasthörer:in'
        GRADUATED = 'graduated', 'Abgeschlossen'
        LEFT = 'left', 'Ausgeschieden'

    APPLICATION = [Status.APPLIED, Status.ADMITTED, Status.WAITLISTED, Status.REJECTED]
    FORMER = [Status.GRADUATED, Status.LEFT]  # with registered terms: studied here once

    person = models.ForeignKey(Person, models.CASCADE, related_name='studies', verbose_name='Person')
    program = models.ForeignKey(Program, models.PROTECT, related_name='studies', verbose_name='Studiengang')
    subject = models.ForeignKey(Subject, models.PROTECT, related_name='studies', verbose_name='Hauptfach')
    focus = models.ForeignKey(
        'curriculum.Module', models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Schwerpunkt',
        help_text='Der gewählte Bereich des Studienplans, wo die Ordnung Schwerpunkte vorsieht.',
    )
    advised = models.ForeignKey(
        'curriculum.Module', models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Empfohlener Schwerpunkt',
        help_text='Die Empfehlung der Lehrenden, etwa aus der Studienfachberatung; wählen wird die oder der Studierende.',
    )
    start_term = models.ForeignKey(Term, models.PROTECT, related_name='intake', verbose_name='Studienbeginn')
    status = models.CharField('Status', max_length=10, choices=Status, default=Status.APPLIED)
    since = models.DateField('Status seit', default=timezone.localdate)
    issued = models.DateField('Zeugnis ausgestellt am', null=True, blank=True)
    grade = models.DecimalField('Gesamtnote', max_digits=2, decimal_places=1, null=True, blank=True, help_text='Wie im Zeugnis, beim Ausstellen festgehalten.')
    teacher = models.ForeignKey(
        Person, models.SET_NULL, null=True, blank=True, related_name='class_studies',
        verbose_name='Hauptfachlehrer:in', help_text='Bis zur Zulassung der Wunsch aus der Bewerbung.',
    )
    prior_terms = models.PositiveSmallIntegerField('Angerechnete Semester', default=0)
    exit_reason = models.CharField('Grund der Exmatrikulation', max_length=1, choices=EXITS, blank=True)
    structured = models.BooleanField('In einem strukturierten Promotionsprogramm', default=False)  # these three for a Promotion
    employed = models.BooleanField('An der Hochschule beschäftigt', default=False)
    cumulative = models.BooleanField('Kumulative Dissertation', default=False, help_text='Aus Veröffentlichungen statt einer Monografie.')
    compensation = models.CharField(
        'Nachteilsausgleich', max_length=200, blank=True,
        help_text='Was der Prüfungsausschuss gewährt hat, ohne Diagnose, z. B. „50 % mehr Bearbeitungszeit bei Klausuren“ (RPSO § 8).',
    )

    class Meta:
        ordering = ['-start_term__start', 'person__last_name']
        verbose_name = 'Studium'
        verbose_name_plural = 'Studien'
        constraints = [models.UniqueConstraint(fields=['person', 'program', 'subject', 'start_term'], name='unique_study')]

    def __str__(self):
        return f'{self.person} · {self.subject} · {self.program}'

    def get_absolute_url(self):
        if self.status in self.APPLICATION:
            return reverse('admissions:application', args=[self.pk])
        return reverse('students:study', args=[self.pk])

    @classmethod
    def find(cls, text):
        """Enrolled studies and guest auditors meant by a name, e-mail or Matrikelnummer, however it is typed; at most five."""
        studies = cls.objects.filter(status__in=[cls.Status.ENROLLED, cls.Status.GUEST]).select_related('person', 'program')
        if number := re.search(r'\d{5,}', text):
            if exact := list(studies.filter(person__matriculation=number.group())):
                return exact
        return list(matching(studies, text, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email', 'subject__name', 'program__degree')[:5])

    @classmethod
    def reachable(cls, person, studies):
        """The pks of `studies` whose page (get_absolute_url) the person may open: their own; a study for its Hauptfachlehrer:in
        and the offices that see or manage files (students:study); an application for the offices that see applications
        and its jury (admissions:application)."""
        from courses.models import Course

        base = cls.objects.filter(pk__in=studies.values('pk'))
        applying, studying = base.filter(status__in=cls.APPLICATION), base.exclude(status__in=cls.APPLICATION)
        found = (
            base.filter(person=person) | studying.filter(teacher=person) | person.within(studying, 'view_files', 'manage_students')
            | person.within(applying, 'view_applications')
            | applying.filter(participations__course__kind=Course.Kind.AUDITION, participations__course__staff=person)
        )
        return set(found.values_list('pk', flat=True))

    def set_status(self, status, reason='', since=None):
        """A new status from `since`, else today; `reason` says why a study ended, as the official statistics count it.
        Whoever thereby starts or stops studying here goes on the list of status changes (Event)."""
        person = self.person
        enrolled = person.studies.filter(status=self.Status.ENROLLED).exists
        was = enrolled()
        self.status, self.since, self.exit_reason = status, since or timezone.localdate(), reason
        self.save(update_fields=['status', 'since', 'exit_reason'])
        if was != enrolled():  # the first study here began, or the last ended; a change of subject is neither
            from .smv import tell

            term = self.start_term if not was else Term.objects.filter(start__lte=self.since).order_by('-start').first()
            tell(Event.objects.create(person=person, kind=Event.Kind.LEFT if was else Event.Kind.ENROLLED, on=self.since, term=term))
        if status in self.FORMER and person.private_email and not person.studies.filter(status__in=[self.Status.ENROLLED, self.Status.GUEST]).exists():
            person.email = person.private_email  # the Hochschul address ends with the study
            person.save(update_fields=['email'])

    @property
    def studienart(self):
        """Erststudium, Zweitstudium, Master and so on, as the official statistics count them: an Erststudium after a degree
        is a Zweitstudium."""
        degree = self.person.degree_exam or self.person.studies.filter(status=self.Status.GRADUATED, since__lt=self.start_term.start).exists()
        return dict(destatis.KINDS)['2' if self.program.kind == '1' and degree else self.program.kind]

    @property
    def state(self):
        """The status as one reads it: a study ended by an Exmatrikulation says so, one left for another subject does not."""
        return 'Exmatrikuliert' if self.status == self.Status.LEFT and self.exit_reason else self.get_status_display()

    def wording(self, key, *after, **values):
        """(subject, body) of a text about this study, its placeholders filled in, the text blocks `after` following (core.texts)."""
        return texts.render(key, *after, **{
            'vorname': self.person.first_name, 'nachname': self.person.last_name, 'studiengang': self.program.title,
            'hauptfach': self.subject, 'semester': self.start_term, 'matrikelnummer': self.person.matriculation or '', **values,
        })

    def notify(self, key, link='', after=(), **values):
        """Issue a Bescheid: it goes to the Postfach and stays in the student file as a printable letter."""
        subject, body = self.wording(key, *after, **values)
        message = Message.send(self.person, subject, body, link, sender=texts.block('absender_bescheide'))
        Document.objects.create(person=self.person, study=self, kind=DocumentType.system('notice'), title=subject, message=message, template=key)

    @property
    def fee_due(self):
        """When a newcomer's first Semesterbeitrag is due: by the Rückmeldefrist, or later for whoever was offered the place
        late, as from the Nachrückliste: the days to accept it, then as many again to pay."""
        return max(self.start_term.deadline, self.since + timedelta(days=2 * Institution.get().accept_days))

    WAITING = ('Versicherungsmeldung', 'Hochschulzugangsberechtigung aus dem Ausland')  # what waits on the insurer or the Hochschule

    def steps(self):
        """missing(), split for the applicant: (what they do, what waits on others)."""
        missing = self.missing()
        return [m for m in missing if not m.startswith(self.WAITING)], [m for m in missing if m.startswith(self.WAITING)]

    def missing(self):
        """What still blocks the enrolment of an admitted applicant."""
        reasons = self.person.missing()
        registration = self.terms.filter(term=self.start_term).first()
        if not registration:
            reasons.insert(0, 'Studienplatz noch nicht angenommen')
        elif not registration.paid:
            reasons.insert(0, 'Semesterbeitrag noch nicht eingegangen')
        if not self.person.entrance:
            reasons.append('Hochschulzugangsberechtigung nicht erfasst')
        elif self.person.abroad and not self.person.entrance_checked:
            reasons.append('Hochschulzugangsberechtigung aus dem Ausland nicht geprüft')
        from admissions.views import checklist  # which imports from here

        reasons += [f'{item["label"]} fehlt' for item in checklist(self) if item['kind'].condition in DocumentType.ENROLMENT and not item['documents']]
        return reasons

    @property
    def semester(self):
        """Fachsemester: credited prior terms plus registered terms so far, leave excluded."""
        return self.prior_terms + self.terms.filter(leave=False, withdrawn=False, term__start__lte=timezone.localdate()).count()

    @property
    def university_semester(self):
        """Hochschulsemester: every term the person has been registered here, leave included, plus semesters elsewhere."""
        here = StudyTerm.objects.filter(study__person=self.person_id, withdrawn=False, term__start__lte=timezone.localdate()).values('term').distinct().count()
        return self.person.external_terms + here

    def record(self):
        """Credited participations, one per module, a graded one where there is one. Exams and theses count once released."""
        best = {}
        rows = (
            self.participations.filter(passed=True, module__isnull=False)
            .exclude(course__kind__in=['exam', 'thesis'], course__released=None)
            .select_related('module', 'course__term')
        )
        for p in rows:
            if p.grade is not None or p.module_id not in best:
                best[p.module_id] = p
        return sorted(best.values(), key=lambda p: p.module.code)

    @staticmethod
    def choose_by(fs, first):
        """When a Schwerpunkt beginning in semester `first` is chosen, `fs` being the Fachsemester now: before its term starts."""
        return (fs < first and Term.upcoming() or Term.current()).start

    def options(self):
        """The Schwerpunkte the student may choose between: until the chosen one has begun, then changes are the office's."""
        found = self.program.choices()
        if self.focus_id and (not found or self.semester >= min(o.first or 0 for o in found)):
            return []
        return found

    def progress(self):
        """(curriculum tree with earned credits, grades and each exam's failed attempts, final grade so far). Each node
        has its `failures`, and `last` when the next attempt is the last the Prüfungsordnung allows."""
        from courses.models import Participation  # which imports from here

        tree = self.program.curriculum({p.module_id: p.grade for p in self.record()}, self.subject_id)
        failed = defaultdict(list)
        for p in Participation.objects.filter(
            study__person=self.person_id, module__program=self.program_id, passed=False, course__kind__in=['exam', 'thesis'], course__released__isnull=False,
        ).select_related('course__term').order_by('course__released'):
            failed[p.module_id].append(p)

        def mark(node):
            node.failures = failed[node.pk]
            node.last = bool(node.failures) and not node.done and len(node.failures) == self.program.attempts - 1
            for child in node.nodes:
                mark(child)

        for root in tree:
            mark(root)
        return tree, weighted(tree, self.program.rounding)


class StudyTerm(Tracked, models.Model):
    """A study registered for one term (Rückmeldung), possibly as leave (Urlaubssemester).

    The fee is paid per person: one paid registration covers all of a person's studies that term.
    """

    owner = 'study.person_id'
    capabilities = ('manage_students',)
    scope = (['study__subject__institute'], ['study__program'])

    study = models.ForeignKey(Study, models.CASCADE, related_name='terms', verbose_name='Studium')
    term = models.ForeignKey(Term, models.PROTECT, related_name='registrations', verbose_name='Semester')
    leave = models.BooleanField('Urlaubssemester', default=False)
    leave_reason = models.CharField('Grund der Beurlaubung', max_length=1, choices=LEAVES, blank=True)
    paid = models.DateField('Beitrag bezahlt am', null=True, blank=True)
    amount = models.DecimalField('Betrag', max_digits=7, decimal_places=2, null=True, blank=True)
    withdrawn = models.BooleanField(
        'Vor Beginn exmatrikuliert', default=False, help_text='Bezahlt, dann vor Beginn des Semesters exmatrikuliert: es zählt nicht, der Beitrag wird erstattet.',
    )
    reference = models.CharField('Beleg oder Verwendungszweck', max_length=140, blank=True)

    class Meta:
        ordering = ['term__start']
        verbose_name = 'Rückmeldung'
        verbose_name_plural = 'Rückmeldungen'
        constraints = [models.UniqueConstraint(fields=['study', 'term'], name='unique_study_term')]

    def __str__(self):
        return f'{self.study} · {self.term}'


class DocumentType(Tracked, Keyed):
    """A kind of document: what applicants hand in, and the Bescheide the Hochschule issues. When the application
    checklist asks for it and what an upload may be are its settings."""

    class Condition(models.TextChoices):
        ALWAYS = 'always', 'immer'
        VIDEO = 'video', 'wenn es eine Videostufe gibt (für die Kommission)'
        DEGREE = 'degree', 'wenn der Studiengang einen Abschluss voraussetzt'
        MINOR = 'minor', 'bei Minderjährigen'
        SUBJECTS = 'subjects', 'bei den gewählten Hauptfächern'
        FOREIGN = 'foreign', 'bei einer Hochschulzugangsberechtigung aus dem nicht deutschsprachigen Ausland'
        APS = 'aps', 'bei einer Hochschulzugangsberechtigung aus China, Indien oder Vietnam'
        NEVER = 'never', 'nicht in der Checkliste'

    ENROLMENT = (Condition.FOREIGN, Condition.APS)  # handed in by the Immatrikulation at the latest, not with the application

    SYSTEM = {  # what the code files itself
        'notice': {'name': 'Bescheid', 'condition': Condition.NEVER},
        'abschlussarbeit': {'name': 'Abschlussarbeit', 'condition': Condition.NEVER, 'file_types': '.pdf'},
        'anerkennung': {'name': 'Nachweis zur Anerkennung', 'condition': Condition.NEVER},
        'attest': {'name': 'Attest', 'condition': Condition.NEVER, 'health': True},  # to a Rücktritt or a Nachteilsausgleich
    }
    capabilities = ('run_auditions',)

    name = models.CharField('Name', max_length=80, unique=True)
    condition = models.CharField('In der Checkliste', max_length=10, choices=Condition, default=Condition.ALWAYS)
    subjects = models.ManyToManyField(Subject, blank=True, related_name='+', verbose_name='Hauptfächer', help_text='Nur für „bei den gewählten Hauptfächern“.')
    required = models.BooleanField('Pflicht', default=True, help_text='Ohne Haken freiwillig: die Bewerbung ist auch ohne vollständig.')
    shared = models.BooleanField(
        'Für alle Bewerbungen', default=False, help_text='Einmal hochgeladen, gilt es für jede Bewerbung der Person, etwa Lebenslauf oder Zeugnisse.',
    )
    jury = models.BooleanField(
        'Für die Kommission', default=False, help_text='Die Prüfungskommission sieht es, etwa Video oder Lebenslauf; alles andere nur die Verwaltung.',
    )
    health = models.BooleanField(
        'Gesundheitsdaten', default=False,
        help_text='Art. 9 DSGVO, etwa ein phoniatrisches Gutachten: öffnen nur die Person und wer über die Bewerbung entscheidet; gelöscht, sobald entschieden ist.',
    )
    note = models.TextField('Hinweis für Bewerbende', blank=True)
    amount = models.DecimalField(
        'Zu überweisen', max_digits=7, decimal_places=2, null=True, blank=True,
        help_text='Für einen Zahlungsbeleg, etwa die Prüfungsgebühr: Bewerbende sehen Betrag und Bankverbindung der Hochschule.',
    )
    file_types = models.CharField('Dateitypen', max_length=120, blank=True, help_text='Endungen, z. B. „.pdf, .jpg“; leer: PDF und Bilder.')
    template = models.TextField(
        'Vordruck', blank=True,
        help_text='Zum Ausdrucken und Unterschreiben, etwa die Einwilligung der Eltern. Platzhalter: {name}, {geburtsdatum}, {studiengang}, {hauptfach}, {hochschule}.',
    )
    max_mb = models.PositiveIntegerField('Höchstens (MB)', null=True, blank=True, help_text='Je Datei; leer: 20 MB.')
    single = models.BooleanField('Nur eine Datei', default=False, help_text='Eine neue ersetzt die alte.')
    max_files = models.PositiveSmallIntegerField('Höchstens Dateien', default=5, help_text='Je Bewerbung, wo nicht nur eine Datei gilt.')
    order = models.PositiveSmallIntegerField('Reihenfolge', default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Unterlagenart'
        verbose_name_plural = 'Unterlagenarten'

    def __str__(self):
        return self.name

    @classmethod
    def system(cls, key):
        return cls.objects.get_or_create(key=key, defaults=cls.SYSTEM[key])[0]

    DEFAULT_TYPES = '.pdf, .jpg, .jpeg, .png, .heic, .webp'  # PDF and photos of a paper
    DEFAULT_MB = 20

    @property
    def extensions(self):
        return tuple(e.strip().lower() for e in (self.file_types or self.DEFAULT_TYPES).replace(';', ',').split(',') if e.strip())

    @property
    def accepted(self):
        """What an upload may be, in words."""
        return f'{self.file_types or "PDF oder Foto"}, ' + ('eine Datei' if self.single else f'bis zu {self.max_files} Dateien') + f' je höchstens {self.limit}'

    @property
    def size(self):
        """The most an upload may hold, in MB."""
        return self.max_mb or self.DEFAULT_MB

    @property
    def limit(self):
        return f'{number_format(self.size / 1024, 1)} GB' if self.size >= 1024 else f'{self.size} MB'

    def refuses(self, file):
        """Why a file cannot be handed in as this kind, or ''."""
        if not file.name.lower().endswith(self.extensions):
            return f'{self} bitte als {self.file_types or "PDF oder Bild"} hochladen.'
        if file.size > self.size * 2 ** 20:
            return f'Die Datei ist größer als {self.limit}. Bitte verkleinern und erneut hochladen.'
        return ''


def stored(document, filename):
    """A random name: nobody finds a file by guessing its address. The title keeps what it was called."""
    return f'dokumente/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class Document(Tracked, models.Model):
    """A file or link a person handed in or received: CV, certificates, audition video, attests, Bescheide."""

    owner = 'person_id'
    capabilities = ('manage_students',)
    scope = (['study__subject__institute'], ['study__program'])

    VIDEO_FILES = ('.mp4', '.m4v', '.mov', '.webm', '.ogv', '.ogg')  # what browsers play

    person = models.ForeignKey(Person, models.CASCADE, related_name='documents', verbose_name='Person')
    study = models.ForeignKey(Study, models.CASCADE, null=True, blank=True, related_name='documents', verbose_name='Studium')
    kind = models.ForeignKey(DocumentType, models.PROTECT, to_field='key', related_name='documents', verbose_name='Art')
    title = models.CharField('Titel', max_length=120)
    file = models.FileField('Datei', upload_to=stored, blank=True)
    url = models.URLField('Link', max_length=2000, blank=True)  # shared video links run long
    message = models.ForeignKey(Message, models.CASCADE, null=True, blank=True, related_name='+', verbose_name='Bescheid')
    template = models.CharField('Vorlage', max_length=40, blank=True, help_text='Bei einem Bescheid: der Text, aus dem er entstand (Einstellungen › Texte).')
    printed = models.DateTimeField('Gedruckt am', null=True, blank=True, help_text='Bei einem Bescheid per Post.')
    declared = models.DateTimeField('Selbstständigkeitserklärung', null=True, blank=True, help_text='Bei einer Abschlussarbeit: wann sie mit ihr abgegeben wurde.')
    consent = models.ForeignKey(
        'core.Consent', models.SET_NULL, null=True, blank=True, related_name='documents', verbose_name='Einwilligung', help_text='Bei Gesundheitsdaten.',
    )
    created = models.DateTimeField('Eingegangen', default=timezone.now)

    class Meta:
        ordering = ['created']
        verbose_name = 'Dokument'
        verbose_name_plural = 'Dokumente'

    def __str__(self):
        return self.title

    @classmethod
    def hand_in(cls, study, kind, file, consent=None):
        """File an upload; where the kind keeps one file only, it replaces the one before."""
        if kind.single:
            for old in study.documents.filter(kind=kind):
                old.drop_file()
                old.delete()
        return cls.objects.create(person=study.person, study=study, kind=kind, title=file.name[:120], file=file, consent=consent)

    @classmethod
    def purge(cls, documents):
        """Delete these documents with their files, but a file another document keeps too, like the demo's placeholder."""
        for d in documents.exclude(file='').exclude(file__in=cls.objects.exclude(pk__in=documents).values('file')):
            d.file.delete(save=False)
        return documents.delete()[0]

    def drop_file(self):
        """Delete the file, unless another document keeps it too, like the demo's placeholder."""
        if self.file and not Document.objects.filter(file=self.file.name).exclude(pk=self.pk).exists():
            self.file.delete(save=False)

    @property
    def href(self):
        if self.message_id:
            return reverse('letter', args=[self.message_id])
        return reverse('admissions:document', args=[self.pk]) if self.file else self.url

    @property
    def playable(self):
        """A video the browser plays right here: an upload, or a link straight to a video file."""
        return (self.file.name if self.file else self.url).lower().split('?')[0].endswith(self.VIDEO_FILES)


class Event(models.Model):
    """Someone began studying here or stopped: the list of such changes that systems outside follow, each at its own
    pace (docs/roadmap.md). The Hochschule's reports to the health insurers come from it (students/smv.py)."""

    class Kind(models.TextChoices):
        ENROLLED = 'immatrikuliert', 'Immatrikulation'
        LEFT = 'exmatrikuliert', 'Exmatrikulation'

    person = models.ForeignKey(Person, models.CASCADE, related_name='events', verbose_name='Person')
    kind = models.CharField('Art', max_length=14, choices=Kind)
    on = models.DateField('Zum', help_text='Der erste oder der letzte Tag immatrikuliert.')
    term = models.ForeignKey(Term, models.PROTECT, related_name='+', verbose_name='Semester')
    at = models.DateTimeField('Erfasst', default=timezone.now)

    class Meta:
        ordering = ['at']
        verbose_name = 'Statuswechsel'
        verbose_name_plural = 'Statuswechsel'

    def __str__(self):
        return f'{self.get_kind_display()} {self.person} zum {self.on:%d.%m.%Y}'


class InsuranceReport(models.Model):
    """A report of the Studierendenmeldeverfahren (§ 199a SGB V, docs/smv.md): a health insurer's about someone's insurance
    (M10 to M13), or the Hochschule's about their studies (M20, M30)."""

    class Reason(models.TextChoices):
        STATUS = '10', 'M10 Versicherungsstatus'
        CHANGE = '11', 'M11 Kassenwechsel'
        ARREARS = '12', 'M12 Beitragsrückstand'
        PAID = '13', 'M13 Rückstand beglichen'
        START = '20', 'M20 Studienbeginn'
        END = '30', 'M30 Studienende'

    class State(models.TextChoices):
        OPEN = 'offen', 'offen'  # an insurer's not matched to anyone yet; the Hochschule's not sent yet
        DONE = 'erledigt', 'erledigt'  # booked to its person; sent
        FAILED = 'abgewiesen', 'abgewiesen'  # the insurer turned the Hochschule's down
        DROPPED = 'verworfen', 'verworfen'  # about nobody here; withdrawn before it went

    INCOMING = [Reason.STATUS, Reason.CHANGE, Reason.ARREARS, Reason.PAID]
    UNINSURED = 'keine'
    STATUSES = [*Person.Insurance.choices, (UNINSURED, 'nicht versichert')]

    reason = models.CharField('Meldung', max_length=2, choices=Reason)
    state = models.CharField('Stand', max_length=10, choices=State, default=State.OPEN)
    person = models.ForeignKey(Person, models.CASCADE, null=True, blank=True, related_name='insurance_reports', verbose_name='Person')
    event = models.ForeignKey(Event, models.SET_NULL, null=True, blank=True, related_name='reports', verbose_name='Statuswechsel')
    cancels = models.ForeignKey('self', models.CASCADE, null=True, blank=True, related_name='cancellations', verbose_name='Storno zu')
    insurer = models.CharField('Krankenkasse', max_length=80)
    insurer_number = models.CharField('Betriebsnummer der Krankenkasse', max_length=8)
    last_name = models.CharField('Nachname', max_length=150, blank=True)  # whom an insurer's report is about, as it says
    first_name = models.CharField('Vorname', max_length=150, blank=True)
    birth_date = models.DateField('Geboren', null=True, blank=True)
    insurance = models.CharField('Versicherungsstatus', max_length=10, choices=STATUSES, blank=True)
    day = models.DateField('Tag', null=True, blank=True, help_text='M11: Beginn der neuen Versicherung; M20: Tag der Einschreibung; M30: Tag der Exmatrikulation.')
    term = models.ForeignKey(Term, models.PROTECT, null=True, blank=True, related_name='+', verbose_name='Semester')
    created = models.DateTimeField('Eingegangen oder erstellt', default=timezone.now)
    due = models.DateField('Zu senden ab', null=True, blank=True)
    done = models.DateTimeField('Verbucht oder versandt', null=True, blank=True)
    by = models.ForeignKey(Person, models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Von Hand durch')
    error = models.CharField('Fehler', max_length=200, blank=True)

    class Meta:
        ordering = ['-created']
        verbose_name = 'Meldung im Studierendenmeldeverfahren'
        verbose_name_plural = 'Meldungen im Studierendenmeldeverfahren'

    def __str__(self):
        return f'{self.get_reason_display()}{" (Storno)" if self.cancels_id else ""}'

    @property
    def incoming(self):
        return self.reason in self.INCOMING


class Disposal(models.Model):
    """The Aussonderung of a former student's file once kept long enough (students/disposal.py): offered to the archive,
    assessed there, then cut down to the Grunddaten. Nothing goes before the archive has assessed it (ThürArchivG)."""

    person = models.OneToOneField(Person, models.CASCADE, related_name='disposal', verbose_name='Person')
    offered = models.DateField('Angeboten am', default=timezone.localdate)
    offered_by = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Angeboten von')
    archival = models.BooleanField('Archivwürdig', null=True)
    assessed = models.DateField('Bewertet am', null=True, blank=True)
    assessed_by = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Bewertet von')
    reduced = models.DateField('Auf die Grunddaten gekürzt am', null=True, blank=True)

    class Meta:
        ordering = ['-offered']
        verbose_name = 'Aussonderung'
        verbose_name_plural = 'Aussonderungen'

    def __str__(self):
        return str(self.person)
