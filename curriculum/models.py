from collections import defaultdict
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from core import destatis
from core.models import Tracked


class Rounding(models.TextChoices):  # what Prüfungsordnungen say about one decimal
    HALF_UP = 'half_up', 'auf eine Nachkommastelle runden'
    CUT = 'cut', 'nach der ersten Nachkommastelle abschneiden'


def rounded(value, rounding):
    return value.quantize(Decimal('0.1'), ROUND_DOWN if rounding == Rounding.CUT else ROUND_HALF_UP)


def weighted(nodes, rounding=Rounding.HALF_UP):
    """Mean of the nodes' grades by their weights, to one decimal; None if nothing counts."""
    graded = [(node.weight, node.grade) for node in nodes if node.weight and node.grade is not None]
    total = sum(weight for weight, _ in graded)
    if not total:
        return None
    return rounded(sum(weight * grade for weight, grade in graded) / total, rounding)


class Subject(Tracked, models.Model):
    """Main subject (Hauptfach)."""

    capabilities = ('edit_curriculum',)
    scope = (['institute'], ['programs'])

    name = models.CharField('Name', max_length=80, unique=True)
    institute = models.ForeignKey('core.Institute', models.SET_NULL, null=True, blank=True, related_name='subjects', verbose_name='Institut')
    english = models.CharField('Englisch', max_length=80, blank=True, help_text='Für das Diploma Supplement.')
    field = models.CharField('Studienfach', max_length=3, choices=destatis.fields, blank=True, help_text='Für die amtliche Statistik, z. B. Orchestermusik.')

    class Meta:
        ordering = ['name']
        verbose_name = 'Hauptfach'
        verbose_name_plural = 'Hauptfächer'

    def __str__(self):
        return self.name


class Program(Tracked, models.Model):
    """A degree programme under one version of its regulations (Studiengang + Prüfungsordnung)."""

    capabilities = ('edit_curriculum',)
    scope = (['subjects__institute'], ['pk'])

    class Level(models.TextChoices):  # as the Diploma Supplement states it
        BACHELOR = 'bachelor', 'Bachelor, erster Abschluss (EQR 6)'
        MASTER = 'master', 'Master, zweiter Abschluss (EQR 7)'
        DIPLOMA = 'diplom', 'Diplom, grundständig (EQR 7)'

    name = models.CharField('Name', max_length=120)
    degree = models.CharField('Abschluss', max_length=40)
    version = models.PositiveSmallIntegerField('PO-Version')
    semesters = models.PositiveSmallIntegerField('Regelstudienzeit')
    subjects = models.ManyToManyField(Subject, blank=True, related_name='programs', verbose_name='Hauptfächer')
    postgraduate = models.BooleanField('Setzt einen Abschluss voraus', default=False, help_text='Bewerbende reichen ihr Abschlusszeugnis ein.')
    attempts = models.PositiveSmallIntegerField('Prüfungsversuche', default=3)
    rounding = models.CharField('Modul- und Gesamtnoten', max_length=8, choices=Rounding, default=Rounding.HALF_UP)
    mean_rounding = models.CharField('Mittel der Prüfenden', max_length=8, choices=Rounding, default=Rounding.CUT)
    honours = models.DecimalField(
        'Mit Auszeichnung bis', max_digits=2, decimal_places=1, null=True, blank=True,
        help_text='Gesamtnote, bis zu der „mit Auszeichnung“ vergeben wird; leer: nie.',
    )
    thesis_weeks = models.PositiveSmallIntegerField(
        'Bearbeitungszeit der Abschlussarbeit (Wochen)', null=True, blank=True, help_text='Ab Ausgabe des Themas; leer: die Abgabe wird von Hand eingetragen.',
    )
    level = models.CharField('Stufe', max_length=8, choices=Level, blank=True, help_text='Für das Diploma Supplement; leer: keines.')
    qualification = models.CharField('Abschluss ausgeschrieben', max_length=120, blank=True, help_text='Wie im Diploma Supplement, z. B. „Bachelor of Music (B.Mus.)“.')
    english = models.CharField('Studiengang auf Englisch', max_length=120, blank=True)
    outcomes = models.TextField('Lernergebnisse auf Englisch', blank=True, help_text='Abschnitt 4.2 des Diploma Supplement.')
    exam = models.CharField('Abschlussprüfung', max_length=2, choices=destatis.degrees, blank=True, help_text='Für die amtliche Statistik.')
    kind = models.CharField(
        'Studienart', max_length=1, choices=destatis.KINDS, default='1',
        help_text='Für die amtliche Statistik; wer schon einen Abschluss hat, studiert ein Erststudium als Zweitstudium.',
    )
    field = models.CharField(
        'Studienfach', max_length=3, choices=destatis.fields, blank=True, help_text='Für die amtliche Statistik; leer: das des Hauptfachs.',
    )
    exam_office = models.CharField(
        'Schlüssel des Prüfungsamts', max_length=7, blank=True,
        help_text='Für die amtliche Statistik, siebenstellig, z. B. 5091601 für das Landesprüfungsamt in Thüringen; leer: das der Hochschule.',
    )

    class Meta:
        ordering = ['-degree', 'name', '-version']
        verbose_name = 'Studiengang'
        verbose_name_plural = 'Studiengänge'
        constraints = [models.UniqueConstraint(fields=['degree', 'name', 'version'], name='unique_program')]

    def __str__(self):
        return f'{self.title} ({self.version})'

    @property
    def title(self):
        """The programme as the Hochschule names it: 'B.Mus. Klavier', but just 'Konzertexamen' where the name says the degree."""
        return self.name if self.degree in self.name else f'{self.degree} {self.name}'

    def get_absolute_url(self):
        return reverse('curriculum:program', args=[self.pk])

    def foci(self):
        """The pks of the Schwerpunkte a student chooses between."""
        return [c.pk for c in self.choices()]

    def choices(self):
        """The Schwerpunkte in plan order: areas of a choice where any one alone carries its credits over several
        semesters, like Künstlerische or Instrumentalpädagogische Vertiefung; not a variant chosen anew each semester.
        Each with `first`, the semester it begins in, and `choice`, the area it is chosen in."""
        found = []

        def walk(node):
            if len(node.nodes) > 1 and all(kid.credits >= node.credits for kid in node.nodes):
                for kid in node.nodes:
                    if kid.nodes and kid.first != kid.last:
                        kid.choice = node
                        found.append(kid)
            for kid in node.nodes:
                walk(kid)

        for root in self.curriculum():
            walk(root)
        return found

    @classmethod
    def planned(cls):
        """Programmes with a Studienplan, which courses, exams and auditions belong to; not a Gasthörerschaft."""
        return cls.objects.filter(modules__isnull=False).distinct()

    @classmethod
    def current(cls):
        """The latest Prüfungsordnung of each planned Studiengang, what one can start now; each with the pks of the
        Hauptfächer it `offered`."""
        newer = cls.objects.filter(degree=models.OuterRef('degree'), name=models.OuterRef('name'), version__gt=models.OuterRef('version'))
        programs = list(cls.planned().exclude(models.Exists(newer)).prefetch_related('subjects'))
        for p in programs:
            p.offered = {s.pk for s in p.subjects.all()}
        return programs

    def curriculum(self, results=None, subject=None):
        """Root modules in study-plan order. Each module gets `nodes` (children), `offered` (their credits),
        `first` and `last` semester, and, given {module_id: grade} of passed modules, `done`, `earned` and `grade`;
        with the Hauptfach, exams waived for it count as done (`excused`)."""
        results = results or {}
        excused = set(self.modules.filter(waived=subject).values_list('pk', flat=True)) if subject else set()
        children = defaultdict(list)
        for module in self.modules.order_by('pk'):  # areas and modules as the plan lists them; what is booked in them by semester
            children[module.parent_id].append(module)
        for kids in children.values():
            kids.sort(key=lambda m: (0, 0, '', m.pk) if m.pk in children else (1, m.semester or 99, m.code, m.pk))

        def walk(module):
            module.nodes = children[module.pk]
            for node in module.nodes:
                walk(node)
            module.offered = sum(node.credits for node in module.nodes)
            if module.nodes:
                semesters = [s for node in module.nodes for s in (node.first, node.last) if s]
                module.first, module.last = (min(semesters), max(semesters)) if semesters else (None, None)
                module.earned = min(module.credits, sum(node.earned for node in module.nodes))
                module.done = module.earned >= module.credits and all(node.done for node in module.nodes if not node.credits)
                module.grade = weighted(module.nodes, self.rounding)
            else:
                module.first = module.last = module.semester
                module.excused = module.pk in excused
                module.done = module.pk in results or module.excused
                module.earned = module.credits if module.done else 0
                module.grade = results.get(module.pk)

        for root in children[None]:
            walk(root)
        return children[None]


class Module(Tracked, models.Model):
    """A node in a programme's curriculum tree: an area, a module, or what is booked in it (a course in one
    semester, an exam).

    Two rules cover all structure. Completion: a leaf is complete when passed; a branch once its completed
    children carry its credits and its children without credits (exams) are passed. Credits equal to the
    children's sum make them all required; fewer make them a choice. Grades: a branch's grade is the weighted
    mean of its children's grades; the final grade is that of the root modules. Weight 0 does not count.
    """

    capabilities = ('edit_curriculum',)
    scope = (['program__subjects__institute'], ['program'])

    program = models.ForeignKey(Program, models.CASCADE, related_name='modules', verbose_name='Studiengang')
    parent = models.ForeignKey('self', models.CASCADE, null=True, blank=True, related_name='children', verbose_name='Übergeordnet')
    code = models.CharField('Kürzel', max_length=20, blank=True)
    title = models.CharField('Titel', max_length=200)
    credits = models.PositiveSmallIntegerField('ECTS')
    semester = models.PositiveSmallIntegerField('Fachsemester', null=True, blank=True)
    exam = models.BooleanField('Prüfungsleistung', default=False, help_text='Eine Prüfung statt Teilnahme oder Studienleistung.')
    final = models.BooleanField(
        'Teil der Abschlussprüfung', default=False,
        help_text='Abschlussprojekt oder -arbeit: ihre Prüfungen bewerten mindestens zwei Prüfende, darunter eine Professur (RPSO § 17 Abs. 5).',
    )
    weight = models.DecimalField(
        'Gewichtung', max_digits=5, decimal_places=2, default=0,
        help_text='Anteil an der Note des übergeordneten Moduls, bei Modulen der obersten Ebene an der Gesamtnote. 0 zählt nicht.',
    )
    description = models.TextField('Beschreibung', blank=True)
    waived = models.ManyToManyField(Subject, blank=True, related_name='+', verbose_name='Entfällt für', help_text='Hauptfächer, für die der Plan diese Prüfung erlässt.')

    class Meta:
        ordering = ['code']
        verbose_name = 'Modul'
        verbose_name_plural = 'Module'
        constraints = [models.UniqueConstraint(fields=['program', 'code'], condition=~models.Q(code=''), name='unique_module_code')]

    def __str__(self):
        return f'{self.code} {self.title}'.strip()

    @classmethod
    def counted(cls):
        """Modules as a Studienplan counts them: those with course work or exams of their own, not the areas above them
        nor what is booked in them."""
        return cls.objects.filter(models.Exists(cls.objects.filter(parent=models.OuterRef('pk'), children=None)))

    def clean(self):
        """A module stays in its programme's tree and never ends up under itself."""
        node = self.parent
        while node:
            if node.program_id != self.program_id:
                raise ValidationError({'parent': 'Gehört zu einem anderen Studiengang.'})
            if node.pk == self.pk:
                raise ValidationError({'parent': 'Ein Modul kann nicht unter sich selbst stehen.'})
            node = node.parent
