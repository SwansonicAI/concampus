"""The Überblick: each area's key figures against the year before, and charts that let the numbers speak for themselves."""
from datetime import datetime, time, timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from django.utils.formats import number_format

from core.models import Term
from courses.models import Course, Participation
from rooms.models import Booking, Room
from students.models import Study, StudyTerm
from .figures import MEASURES, OUTCOMES, OVER, WEEKDAYS, admitted, semesters, table, tally, window
from .charts import Query, chart, figure

HOURS = range(8, 22)  # the day the rooms are open


def percent(part, whole):
    return round(100 * part / whole) if whole else 0


def ago(series):
    """The same semester a year before."""
    return series[-3] if len(series) > 2 else None


def stat(label, value=None, shown=None, before=None, series=(), unit=''):
    """A key figure with the year before and, as a sparkline, its course. Shares show the year before, counts also the change."""
    change = percent(value - before, before) if before and not unit else None
    peak, low = max(series, default=0), min(series, default=0)
    points = ' '.join(f'{100 * i / (len(series) - 1):.1f},{22 - 20 * (v - low) / ((peak - low) or 1):.1f}' for i, v in enumerate(series)) if len(series) > 1 else ''
    return {
        'label': label, 'shown': shown or f'{number_format(value, 0, force_grouping=True)}{unit}', 'points': points,
        'change': change, 'before': before and (f'{number_format(before, 0, force_grouping=True)}{unit}'),
    }


def rated(query, person, key, good, phrase):
    """Bars of the first dimension, each with the share of `good` outcomes beside it."""
    t = table(query.measure, [query.keys[0], key], query.terms, query.where)
    found = chart(query, person)
    for bar, r in zip(found['bars'], found['table'].rows):
        bar['extra'] = f'{percent(sum(t.cells.get((r, g), 0) for g in good), t.row_totals.get(r, 0))} % {phrase}'
    return found


def students(person):
    m = MEASURES['studierende']
    terms = window(m, 10)
    if not terms:
        return None
    total, first, abroad, regular = (tally(m, ['semester'], terms, where) for where in [{}, {'fachsemester': 1}, {'herkunft': 'international'}, {'regelstudienzeit': OVER[0]}])
    series = [total.get(t.pk, 0) for t in terms]
    starters = [first.get(t.pk, 0) for t in terms]
    shares = [percent(abroad.get(t.pk, 0), n) for t, n in zip(terms, series)]
    within = [percent(regular.get(t.pk, 0), n) for t, n in zip(terms, series)]
    now = terms[-1]
    return {
        'title': 'Studierende', 'when': now.name,
        'stats': [
            stat('Studierende', series[-1], before=ago(series)),
            stat('im 1. Fachsemester', starters[-1], before=ago(starters), series=starters[-1::-2][::-1]),
            stat('mit ausländischer Staatsangehörigkeit', shares[-1], before=ago(shares), series=shares, unit=' %'),
            stat('in der Regelstudienzeit', within[-1], before=ago(within), series=within, unit=' %'),
        ],
        'charts': [
            chart(Query(m, ['semester'], zeit='j10'), person, 'Studierende je Semester') | {'wide': True},
            chart(Query(m, ['studiengang']), person, f'nach Studiengang, {now}'),
            chart(Query(m, ['staat']), person, f'nach Staatsangehörigkeit, {now}'),
        ],
    }


def applications(person):
    m = MEASURES['bewerbungen']
    terms = semesters(m)
    now = Term.current()
    rounds = [t for t in terms if t.start <= now.start]
    if not rounds:
        return None
    current, before = rounds[-1], ago(rounds)
    by_term = tally(m, ['semester'], terms)
    outcome = {t: tally(m, ['ergebnis'], [t]) for t in (current, before) if t}

    def rates(t):
        found = outcome.get(t, {})
        offers = sum(found.get(o, 0) for o in OUTCOMES[3:])
        return percent(offers, sum(found.values())), percent(found.get(OUTCOMES[5], 0), offers)

    (admit, accept), (admit_before, accept_before) = rates(current), rates(before)
    stats = [
        stat(f'Bewerbungen zum {current}', by_term.get(current.pk, 0), before=by_term.get(before.pk) if before else None),
        stat('Zulassungsquote', admit, before=admit_before, unit=' %'),
        stat('Annahmequote', accept, before=accept_before, unit=' %'),
    ]
    if terms[-1] != current:  # a round still open
        running = terms[-1]
        stats.append(stat(f'Bewerbungen zum {running}, laufend', by_term.get(running.pk, 0), before=by_term.get(rounds[-2].pk) if len(rounds) > 1 else None))
    return {
        'title': 'Bewerbung', 'when': f'Runde {current}',
        'stats': stats,
        'charts': [
            chart(Query(m, ['studienjahr'], zeit='j10'), person, 'Bewerbungen je Studienjahr'), funnel(current),
            rated(Query(m, ['hauptfach'], zeit=str(current.pk)), person, 'ergebnis', OUTCOMES[3:], 'zugelassen') | {'title': f'nach Hauptfach, {current}', 'wide': True},
        ],
    }


def funnel(term):
    """The way through the Eignungsprüfung: who applied, sat the main subject, passed it, was offered a place, took it."""
    hall = Participation.objects.filter(study=OuterRef('pk'), course__kind=Course.Kind.AUDITION, course__video=False, course__subject__isnull=False)
    found = (
        Study.objects.filter(start_term=term)
        .filter(Exists(Participation.objects.filter(study=OuterRef('pk'), course__kind=Course.Kind.AUDITION, course__subject__isnull=False)))
        .annotate(sat=Exists(hall), passed=Exists(hall.filter(passed=True)), took=Exists(StudyTerm.objects.filter(study=OuterRef('pk'))))
        .aggregate(  # named apart from the annotations they count
            all=Count('pk'), n_sat=Count('pk', filter=Q(sat=True)), n_passed=Count('pk', filter=Q(passed=True)),
            n_offered=Count('pk', filter=admitted()), n_took=Count('pk', filter=Q(took=True)),
        )
    )
    steps = [('beworben', 'all'), ('zur Hauptfachprüfung', 'n_sat'), ('Hauptfach bestanden', 'n_passed'), ('zugelassen', 'n_offered'), ('Platz angenommen', 'n_took')]
    return {'title': f'Weg durch die Eignungsprüfung, {term}', 'bars': [
        {'label': label, 'shown': number_format(found[k], 0, force_grouping=True), 'width': percent(found[k], found['all']), 'extra': f'{percent(found[k], found["all"])} %'}
        for label, k in steps
    ] if found['all'] else []}


def exams(person):
    m = MEASURES['pruefungen']
    last = semesters(m)[-1:]
    if not last:
        return None
    term = last[0]
    result = tally(m, ['ergebnis'], last)
    count = sum(result.values())
    mean = tally(MEASURES['durchschnitt'], (), last)
    return {
        'title': 'Prüfungen', 'when': term.name, 'half': True,
        'stats': [
            stat('Prüfungsergebnisse', count),
            stat('bestanden', percent(result.get(True, 0), count), unit=' %'),
            stat('Durchschnittsnote', shown=figure(mean, MEASURES['durchschnitt'])),
        ],
        'charts': [rated(Query(m, ['studiengang'], zeit=str(term.pk)), person, 'ergebnis', [True], 'bestanden') | {'title': 'nach Studiengang'}],
    }


def teaching(person):
    term = Term.current()
    courses = Course.objects.filter(term=term, kind=Course.Kind.COURSE).annotate(n=Count('participations'))
    sizes = list(courses.values_list('n', 'capacity'))
    lessons = Course.objects.filter(term=term, kind=Course.Kind.LESSON).count()
    taken = sum(n for n, _ in sizes)
    query = Query(MEASURES['veranstaltungen'], ['groesse'], {'art': Course.Kind.COURSE})
    return {
        'title': 'Lehre', 'when': term.name, 'half': True,
        'stats': [
            stat('Lehrveranstaltungen', len(sizes)),
            stat('Klassen im Einzelunterricht', lessons),
            stat('Belegungen der Lehrveranstaltungen', taken),
            stat('Teilnehmende je Lehrveranstaltung', shown=number_format(taken / len(sizes), 1) if sizes else '–'),
        ],
        'charts': [chart(query, person, 'Lehrveranstaltungen nach Teilnehmenden')],
    }


def rooms(person):
    """The last seven days in the rooms the Institute give out themselves, worked out per hour."""
    today = timezone.localdate()
    days = [today - timedelta(days=n) for n in range(6, -1, -1)]
    start = timezone.make_aware(datetime.combine(days[0], time()))
    end = start + timedelta(days=7)
    rooms = {r.pk: r for r in Room.objects.filter(function__central=False).select_related('function')}
    if not rooms:
        return None
    used, by_room, booked, students = {}, dict.fromkeys(rooms, 0), 0, 0
    for room, begin, finish, by_students in Booking.objects.filter(
        room__in=rooms, category__blocks=True, start__lt=end, end__gt=start,
    ).values_list('room', 'start', 'end', 'category__students'):
        begin, finish = timezone.localtime(max(begin, start)), timezone.localtime(min(finish, end))
        booked += (finish - begin).total_seconds() / 3600
        students += by_students
        hour = begin.replace(minute=0, second=0, microsecond=0)
        while hour < finish:
            minutes = (min(finish, hour + timedelta(hours=1)) - max(begin, hour)).total_seconds() / 60
            if hour.hour in HOURS:
                used[(hour.date(), hour.hour)] = used.get((hour.date(), hour.hour), 0) + minutes
                by_room[room] += minutes
            hour += timedelta(hours=1)
    open_ = len(HOURS) * 60 * len(days)
    grid = [{
        'label': f'{WEEKDAYS[day.isoweekday()]} {day:%d.%m.}',
        'cells': [{'share': (share := percent(used.get((day, h), 0), 60 * len(rooms))), 'shade': min(100, share), 'tip': f'{WEEKDAYS[day.isoweekday()]} {h}–{h + 1} Uhr: {share} % belegt'} for h in HOURS],
    } for day in days]
    peak = max(((day, h) for day in days for h in HOURS), key=lambda k: used.get(k, 0))

    def bars(group):
        found = {}
        for pk, room in rooms.items():
            minutes, count = found.get(group(room), (0, 0))
            found[group(room)] = (minutes + by_room[pk], count + 1)
        return [
            {'label': g, 'shown': f'{percent(m, n * open_)} %', 'width': percent(m, n * open_), 'extra': f'{n} Räume'}
            for g, (m, n) in sorted(found.items(), key=lambda kv: -kv[1][0] / kv[1][1])
        ]

    return {
        'title': 'Räume', 'when': f'{days[0]:%d.%m.} bis {days[-1]:%d.%m.%Y}, {HOURS[0]}–{HOURS[-1] + 1} Uhr',
        'stats': [
            stat('belegt im Durchschnitt', percent(sum(by_room.values()), open_ * len(rooms)), unit=' %'),
            stat('gebuchte Stunden', round(booked)),
            stat('Buchungen von Studierenden', students),
            stat('am vollsten', shown=f'{WEEKDAYS[peak[0].isoweekday()]} {peak[1]}–{peak[1] + 1} Uhr'),
        ],
        'charts': [
            {'title': 'Belegung der Instituts- und Übungsräume je Stunde', 'grid': {'hours': list(HOURS), 'days': grid}},
            {'title': 'nach Raumfunktion', 'bars': bars(lambda r: r.function.name)},
        ],
    }


def overview(person):
    sections = [students(person), applications(person), exams(person), teaching(person), rooms(person)]
    return {'sections': [s for s in sections if s]}
