"""What the Auswertungen count and by what. One definition serves the overview, the free query and the records
behind every number: a measure is a set of rows and how to count them, a dimension a way to split them."""
from dataclasses import dataclass, field
from decimal import Decimal
from functools import reduce
from operator import or_

from django.db.models import Avg, Case, CharField, Count, Exists, ExpressionWrapper, F, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, ExtractIsoWeekDay, ExtractYear
from django.db.models.lookups import GreaterThan, LessThan
from django.urls import reverse
from django.utils import timezone
from django.utils.choices import flatten_choices

from core import destatis
from core.models import Institute, Person, Term
from courses.models import Course, Participation
from curriculum.models import Program, Subject
from rooms.models import Booking, BookingCategory, Room, RoomFunction
from students.models import Study, StudyTerm

OTHERS = 'weitere'  # where the long tail of a dimension is folded


def named(model, text=str, key='pk', order=None):
    """Labels from a model's rows, in the given order (by default largest first, see `ordered`)."""
    def labels(values):
        rows = model.objects.filter(**{f'{key}__in': values})
        return {getattr(o, key): text(o) for o in (rows.order_by(order) if order else rows)}
    return labels


def listed(pairs):
    labels = dict(pairs)
    return lambda values: labels


def itself(text=str, kind=None):
    """Labels of plain values; `kind` reads them back from a link."""
    return lambda values: {v: text(v) for v in sorted({kind(v) if kind else v for v in values if v is not None})}


@dataclass
class Dimension:
    title: str
    path: str = ''  # a field; or, for what is worked out, `expression`
    labels: object = itself()
    expression: object = None
    ordered: bool = False  # in the labels' order, not largest first
    time: bool = False  # across time: shown as columns, a label opens that semester
    columns: bool = False  # a distribution, shown as columns
    top: int = 0  # the largest few, the rest folded into 'weitere'

    def value(self):
        return self.expression if self.expression is not None else F(self.path)


@dataclass
class Measure:
    key: str
    title: str  # what one counts: 'Studierende'
    rows: object  # () -> queryset
    value: object  # the aggregate
    dimensions: dict
    capability: str  # to see the records behind a number
    objects: object  # rows -> the records behind them
    describe: object  # record -> (title, detail, url)
    records: str = ''  # what they are called, where not as the measure
    term: str = ''  # path of the semester a row belongs to; '' for bookings, which have their dates
    default: str = ''  # what to split by first
    drill: str = ''  # where a label leads: its course over time, or a closer look
    unit: str = ''
    decimals: int = 0
    convert: object = None

    def dimension(self, key):
        return self.dimensions.get(key)


# Shared dimensions, for rows that reach a study through `to`

def of_study(to=''):
    p = f'{to}__' if to else ''
    return {
        'studiengang': Dimension('Studiengang', f'{p}program', named(Program, lambda o: f'{o.degree} {o.name}')),
        'abschluss': Dimension('Abschluss', f'{p}program__degree'),
        'hauptfach': Dimension('Hauptfach', f'{p}subject', named(Subject)),
        'institut': Dimension('Institut', f'{p}subject__institute', named(Institute)),
        'geschlecht': Dimension('Geschlecht', f'{p}person__gender', listed(Person.Gender.choices)),
        'herkunft': Dimension('Staatsangehörigkeit', expression=Case(
            When(**{f'{p}person__nationality': '000'}, then=Value('deutsch')), default=Value('international'), output_field=CharField(),
        )),
        'staat': Dimension('Staatsangehörigkeit (einzeln)', f'{p}person__nationality', listed(destatis.COUNTRIES), top=10),
        'zugang': Dimension('Hochschulzugang', f'{p}person__entrance', listed(flatten_choices(destatis.ENTRANCE)), top=8),
    }


TERM = named(Term, order='start')
FS = ExpressionWrapper(  # Fachsemester in that term: credited terms plus the registered ones so far, leave excluded
    Subquery(
        StudyTerm.objects.filter(study=OuterRef('study'), leave=False, term__start__lte=OuterRef('term__start'))
        .order_by().values('study').annotate(n=Count('pk')).values('n')
    ) + F('study__prior_terms'),
    output_field=IntegerField(),
)
OVER = ['in der Regelstudienzeit', '1–2 Semester darüber', 'mehr als 2 darüber']
OUTCOMES = ['offen', 'Nachrückliste', 'abgelehnt', 'zugelassen', 'nicht angenommen', 'immatrikuliert']
SIZES = ['0', '1–2', '3–5', '6–10', '11–20', '21–50', 'über 50']
WEEKDAYS = {1: 'Mo', 2: 'Di', 3: 'Mi', 4: 'Do', 5: 'Fr', 6: 'Sa', 7: 'So'}


def grade(value):
    return f'{value:.1f}'.replace('.', ',')


def admitted():
    """Applications that brought an offer: what happened after that is the applicant's choice."""
    return ~Q(status__in=[Study.Status.APPLIED, Study.Status.WAITLISTED, Study.Status.REJECTED])


def studies(rows):
    return rows.select_related('person', 'program', 'subject').order_by('person__last_name', 'person__first_name')


def study(s):
    return s.person.name, f'{s.program.degree} {s.program.name} · {s.subject} · {s.get_status_display()}', s.get_absolute_url()


def participations(rows):
    return rows.select_related('study__person', 'course__term').order_by('course__title', 'study__person__last_name')


def participation(p):
    return p.study.person.name, f'{p.course.title} · {p.course.term}' + (f' · {grade(p.grade)}' if p.grade is not None else ''), p.course.get_absolute_url()


MEASURES = {m.key: m for m in [
    Measure(
        'studierende', 'Studierende',
        rows=lambda: StudyTerm.objects.exclude(study__status__in=[Study.Status.ADMITTED, Study.Status.GUEST]).filter(term__start__lte=timezone.localdate()),
        value=Count('study__person', distinct=True), term='term', default='studiengang', drill='semester', capability='view_students',
        objects=lambda rows: studies(Study.objects.filter(pk__in=rows.values('study'))), describe=study,
        dimensions={
            'semester': Dimension('Semester', 'term', TERM, ordered=True, time=True),
            **of_study('study'),
            'fachsemester': Dimension('Fachsemester', expression=FS, labels=itself(kind=int), ordered=True, columns=True),
            'regelstudienzeit': Dimension('Regelstudienzeit', expression=Case(
                When(GreaterThan(FS, F('study__program__semesters') + 2), then=Value(OVER[2])),
                When(GreaterThan(FS, F('study__program__semesters')), then=Value(OVER[1])),
                default=Value(OVER[0]), output_field=CharField(),
            ), labels=listed((v, v) for v in OVER), ordered=True),
            'urlaub': Dimension('Urlaubssemester', 'leave', listed([(False, 'studierend'), (True, 'beurlaubt')]), ordered=True),
            'beginn': Dimension('Studienbeginn', 'study__start_term', TERM, ordered=True, columns=True),
        },
    ),
    Measure(
        'bewerbungen', 'Bewerbungen',
        rows=lambda: Study.objects.exclude(status=Study.Status.GUEST),
        value=Count('pk'), term='start_term', default='hauptfach', drill='studienjahr', capability='view_applications',
        objects=studies, describe=study,
        dimensions={
            'semester': Dimension('Semester', 'start_term', TERM, ordered=True, time=True),
            'studienjahr': Dimension('Studienjahr', expression=Case(
                When(start_term__start__month__gte=10, then=ExtractYear('start_term__start')), default=ExtractYear('start_term__start') - 1,
            ), labels=itself(lambda y: f'{y}/{(y + 1) % 100:02d}', int), ordered=True, time=True),
            **of_study(),
            'ergebnis': Dimension('Ergebnis', expression=Case(
                When(status=Study.Status.APPLIED, then=Value(OUTCOMES[0])),
                When(status=Study.Status.WAITLISTED, then=Value(OUTCOMES[1])),
                When(status=Study.Status.REJECTED, then=Value(OUTCOMES[2])),
                When(status=Study.Status.ADMITTED, then=Value(OUTCOMES[3])),
                When(Q(status=Study.Status.LEFT) & ~Exists(StudyTerm.objects.filter(study=OuterRef('pk'))), then=Value(OUTCOMES[4])),
                default=Value(OUTCOMES[5]), output_field=CharField(),
            ), labels=listed((v, v) for v in OUTCOMES), ordered=True),
        },
    ),
    *[Measure(
        key, title,
        rows=lambda: Participation.objects.filter(course__kind=Course.Kind.EXAM, course__released__isnull=False, passed__isnull=False),
        value=value, term='course__term', default='studiengang', drill='semester', capability='manage_exams', objects=participations, describe=participation,
        decimals=decimals, records='Prüfungsergebnisse',
        dimensions={
            'semester': Dimension('Semester', 'course__term', TERM, ordered=True, time=True),
            **{k: d for k, d in of_study('study').items() if k in ('studiengang', 'abschluss', 'hauptfach', 'institut')},
            'ergebnis': Dimension('Ergebnis', 'passed', listed([(True, 'bestanden'), (False, 'nicht bestanden')]), ordered=True),
            **({'note': Dimension('Note', 'grade', itself(grade, Decimal), ordered=True, columns=True)} if key == 'pruefungen' else {}),
        },
    ) for key, title, value, decimals in [
        ('pruefungen', 'Prüfungsergebnisse', Count('pk'), 0),
        ('durchschnitt', 'Durchschnittsnote', Avg('grade', filter=Q(course__pass_mark__isnull=True)), 1),
    ]],
    Measure(
        'belegungen', 'Belegungen',
        rows=lambda: Participation.objects.filter(course__kind__in=[Course.Kind.COURSE, Course.Kind.LESSON]),
        value=Count('pk'), term='course__term', default='art', drill='semester', capability='manage_courses', objects=participations, describe=participation,
        dimensions={
            'semester': Dimension('Semester', 'course__term', TERM, ordered=True, time=True),
            'art': Dimension('Art', 'course__kind', listed(Course.Kind.choices)),
            **{k: d for k, d in of_study('study').items() if k in ('studiengang', 'abschluss', 'hauptfach', 'institut')},
        },
    ),
    Measure(
        'veranstaltungen', 'Veranstaltungen',
        rows=lambda: Course.objects.filter(kind__in=[Course.Kind.COURSE, Course.Kind.LESSON]),
        value=Count('pk'), term='term', default='groesse', drill='semester', capability='manage_courses',
        objects=lambda rows: rows.annotate(size=Count('participations')).order_by('title'),
        describe=lambda c: (c.title, f'{c.get_kind_display()} · {c.term} · {c.size} Teilnehmende', c.get_absolute_url()),
        dimensions={
            'semester': Dimension('Semester', 'term', TERM, ordered=True, time=True),
            'art': Dimension('Art', 'kind', listed(Course.Kind.choices)),
            'groesse': Dimension('Teilnehmende', expression=reduce(
                lambda case, bound: Case(When(LessThan(Coalesce(Subquery(
                    Participation.objects.filter(course=OuterRef('pk')).order_by().values('course').annotate(n=Count('pk')).values('n')
                ), 0), bound[0]), then=Value(bound[1])), default=case, output_field=CharField()),
                reversed(list(zip([1, 3, 6, 11, 21, 51], SIZES))), Value(SIZES[-1]),
            ), labels=listed((v, v) for v in SIZES), ordered=True, columns=True),
        },
    ),
    Measure(
        'raumstunden', 'Raumstunden',
        rows=lambda: Booking.objects.filter(category__blocks=True),
        value=Sum(F('end') - F('start')), default='funktion', drill='raum', capability='view_bookings', unit='Std.', records='Buchungen',
        convert=lambda span: round(span.total_seconds() / 3600) if span else 0,
        objects=lambda rows: rows.select_related('room', 'category', 'course').order_by('start'),
        describe=lambda b: (b.label, f'{b.room} · {timezone.localtime(b.start):%d.%m.%Y %H:%M}', reverse('rooms:booking', args=[b.pk])),
        dimensions={
            'funktion': Dimension('Raumfunktion', 'room__function', named(RoomFunction, key='key')),
            'gebaeude': Dimension('Gebäude', 'room__building'),
            'raum': Dimension('Raum', 'room', named(Room)),
            'kategorie': Dimension('Kategorie', 'category', named(BookingCategory, key='key')),
            'wochentag': Dimension('Wochentag', expression=ExtractIsoWeekDay('start'), labels=listed(WEEKDAYS.items()), ordered=True, columns=True),
        },
    ),
]}


def semesters(measure):
    """The semesters with something to count, up to the last one (for applications, the round still open)."""
    last = measure.rows().order_by(f'-{measure.term}__start').values_list(f'{measure.term}__start', flat=True).first() if measure.term else timezone.localdate()
    return list(Term.objects.filter(start__lte=last).order_by('start')) if last else []


def window(measure, years=None):
    """The last `years` years of semesters, whole Studienjahre; all of them without."""
    terms = semesters(measure)
    return terms[-2 * years:] if years else terms


def within(measure, rows, terms):
    if measure.term:
        return rows.filter(**{f'{measure.term}__in': terms})
    return rows.filter(reduce(or_, (Q(start__date__range=(t.start, t.end)) for t in terms), Q(pk=None)))


def narrowed(measure, terms=None, where=None, keys=()):
    """The measure's rows in these semesters, filtered by {dimension: value} and with the dimensions in `keys` worked out."""
    rows = measure.rows()
    where = where or {}
    keys = {*keys, *where}
    rows = rows.annotate(**{f'd_{k}': measure.dimensions[k].value() for k in keys}) if keys else rows
    rows = rows.filter(**{f'd_{k}': v for k, v in where.items()})
    return within(measure, rows, terms) if terms is not None else rows


@dataclass
class Table:
    """A measure split by one or two dimensions: cells, totals, and the labels of each value in order."""
    measure: Measure
    dims: list
    rows: dict = field(default_factory=dict)  # value -> label, in order
    cols: dict = field(default_factory=dict)
    cells: dict = field(default_factory=dict)  # (row value, col value) or row value -> number
    row_totals: dict = field(default_factory=dict)
    col_totals: dict = field(default_factory=dict)
    total: float = 0

    @property
    def peak(self):
        return max([v for v in (self.cells if len(self.dims) > 1 else self.row_totals).values() if v] or [1])


def tally(measure, keys=(), terms=None, where=None):
    """The measure counted per value of the dimensions in `keys` (none, one or two)."""
    rows = narrowed(measure, terms, where, keys)
    number = measure.convert or (lambda v: v or 0)
    if not keys:
        return number(rows.aggregate(v=measure.value)['v'])
    found = rows.values(*(f'd_{k}' for k in keys)).annotate(v=measure.value).order_by()
    return {tuple(r[f'd_{k}'] for k in keys) if len(keys) > 1 else r[f'd_{keys[0]}']: number(r['v']) for r in found}


def table(measure, keys, terms=None, where=None):
    dims = [measure.dimensions[k] for k in keys]
    t = Table(measure, dims, total=tally(measure, (), terms, where))
    t.row_totals = tally(measure, keys[:1], terms, where)
    t.rows = arrange(dims[0], t.row_totals)
    if len(keys) > 1:
        t.col_totals = tally(measure, keys[1:], terms, where)
        t.cols = arrange(dims[1], t.col_totals)
        t.cells = tally(measure, keys, terms, where)
        t.cells = fold(dims[0], t.rows, fold(dims[1], t.cols, t.cells, 1), 0)
        t.col_totals = fold(dims[1], t.cols, t.col_totals)
    t.row_totals = fold(dims[0], t.rows, t.row_totals)
    return t


def arrange(dim, totals):
    """Labels in reading order: as the dimension orders them, else largest first; the tail folded where it is long."""
    labels = dim.labels([v for v in totals if v is not None])
    if dim.ordered:
        order = [v for v in labels if v in totals] + [v for v in totals if v not in labels]
    else:
        order = sorted(totals, key=lambda v: -totals[v])
    if dim.top and len(order) > dim.top + 1:
        order = order[:dim.top] + [OTHERS]
    return {v: OTHERS if v == OTHERS else labels.get(v, 'ohne Angabe') for v in order}


def fold(dim, kept, counts, at=None):
    """Counts of the values not kept summed into 'weitere'. A person has one value each, so the sum is right."""
    if OTHERS not in kept:
        return counts
    folded = {}
    for key, n in counts.items():
        value = key if at is None else key[at]
        if value not in kept:
            key = OTHERS if at is None else tuple(OTHERS if i == at else k for i, k in enumerate(key))
        folded[key] = folded.get(key, 0) + n
    return folded
