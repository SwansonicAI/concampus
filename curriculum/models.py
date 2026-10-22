from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from core.models import Tracked


def weighted(nodes):
    """Mean of the nodes' grades by their weights, to one decimal rounded half up (RPSO § 15); None if nothing counts."""
    graded = [(node.weight, node.grade) for node in nodes if node.weight and node.grade is not None]
    total = sum(weight for weight, _ in graded)
    if not total:
        return None
    return (sum(weight * grade for weight, grade in graded) / total).quantize(Decimal('0.1'), ROUND_HALF_UP)


class Subject(Tracked, models.Model):
    """Main subject (Hauptfach)."""

    capabilities = ('edit_curriculum',)

    name = models.CharField('Name', max_length=80, unique=True)
    institute = models.ForeignKey('core.Institute', models.SET_NULL, null=True, blank=True, related_name='subjects', verbose_name='Institut')

    class Meta:
        ordering = ['name']
        verbose_name = 'Hauptfach'
        verbose_name_plural = 'Hauptfächer'

    def __str__(self):
        return self.name


class Program(Tracked, models.Model):
    """A degree programme under one version of its regulations (Studiengang + Prüfungsordnung)."""

    capabilities = ('edit_curriculum',)

    name = models.CharField('Name', max_length=120)
    degree = models.CharField('Abschluss', max_length=40)
    version = models.PositiveSmallIntegerField('PO-Version')
    semesters = models.PositiveSmallIntegerField('Regelstudienzeit')
    subjects = models.ManyToManyField(Subject, blank=True, related_name='programs', verbose_name='Hauptfächer')

    class Meta:
        ordering = ['-degree', 'name', '-version']
        verbose_name = 'Studiengang'
        verbose_name_plural = 'Studiengänge'
        constraints = [models.UniqueConstraint(fields=['degree', 'name', 'version'], name='unique_program')]

    def __str__(self):
        return f'{self.degree} {self.name} ({self.version})'

    def get_absolute_url(self):
        return reverse('curriculum:program', args=[self.pk])

    def curriculum(self, results=None):
        """Root modules in study-plan order. Each module gets `nodes` (children), `offered` (their credits),
        `first` and `last` semester, and, given {module_id: grade} of passed modules, `done`, `earned` and `grade`."""
        results = results or {}
        children = defaultdict(list)
        for module in self.modules.order_by(models.F('semester').asc(nulls_last=True), 'code'):
            children[module.parent_id].append(module)

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
                module.grade = weighted(module.nodes)
            else:
                module.first = module.last = module.semester
                module.done = module.pk in results
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

    program = models.ForeignKey(Program, models.CASCADE, related_name='modules', verbose_name='Studiengang')
    parent = models.ForeignKey('self', models.CASCADE, null=True, blank=True, related_name='children', verbose_name='Übergeordnet')
    code = models.CharField('Kürzel', max_length=20, blank=True)
    title = models.CharField('Titel', max_length=200)
    credits = models.PositiveSmallIntegerField('ECTS')
    semester = models.PositiveSmallIntegerField('Fachsemester', null=True, blank=True)
    exam = models.BooleanField('Prüfungsleistung', default=False, help_text='Eine Prüfung statt Teilnahme oder Studienleistung.')
    weight = models.DecimalField(
        'Gewichtung', max_digits=5, decimal_places=2, default=0,
        help_text='Anteil an der Note des übergeordneten Moduls, bei Modulen der obersten Ebene an der Gesamtnote. 0 zählt nicht.',
    )
    description = models.TextField('Beschreibung', blank=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'Modul'
        verbose_name_plural = 'Module'
        constraints = [models.UniqueConstraint(fields=['program', 'code'], condition=~models.Q(code=''), name='unique_module_code')]

    def __str__(self):
        return f'{self.code} {self.title}'.strip()

    def clean(self):
        """A module stays in its programme's tree and never ends up under itself."""
        node = self.parent
        while node:
            if node.program_id != self.program_id:
                raise ValidationError({'parent': 'Gehört zu einem anderen Studiengang.'})
            if node.pk == self.pk:
                raise ValidationError({'parent': 'Ein Modul kann nicht unter sich selbst stehen.'})
            node = node.parent
