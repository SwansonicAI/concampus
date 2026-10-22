"""The Überblick: each area's key figures against the year before, its course as charts, and what stands out."""
from datetime import datetime, time, timedelta

from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone
from django.utils.formats import number_format

from core.apps import plural
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


def students(person, insights):
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
    over = tally(m, ['regelstudienzeit'], [now]).get(OVER[2], 0)
    if over:
        q = Query(m, ['studiengang'], {'regelstudienzeit': OVER[2]})
        insights.append({'text': f'{over} Studierende sind mehr als zwei Semester über der Regelstudienzeit.', 'url': q.url('analytics:records') if m.capability in person.roles else q.url()})
    return {
        'title': 'Studierende', 'when': now.name,
        'stats': [
            stat('Studierende', series[-1], before=ago(series), series=series),
            stat('im 1. Fachsemester', starters[-1], before=ago(starters), series=starters[-1::-2][::-1]),
            stat('mit ausländischer Staatsangehörigkeit', shares[-1], before=ago(shares), series=shares, unit=' %'),
            stat('in der Regelstudienzeit', within[-1], before=ago(within), series=within, unit=' %'),
        ],
        'charts': [
            chart(Query(m, ['semester'], zeit='j10'), person, 'Studierende je Semester'),
            chart(Query(m, ['studiengang']), person, f'nach Studiengang, {now}'),
            chart(Query(m, ['fachsemester']), person, f'nach Fachsemester, {now}'),
        ],
    }


def applications(person, insights):
    m = MEASURES['bewerbungen']
    terms = semesters(m)
    now = Term.current()
    rounds = [t for t in terms if t.start <= now.start]
    if not rounds:
        return None
    current, before = rounds[-1], ago(rounds)
    by_term = tally(m, ['semester'], terms)
    years = tally(m, ['studienjahr'], window(m, 10))
    outcome = {t: tally(m, ['ergebnis'], [t]) for t in (current, before) if t}

    def rates(t):
        found = outcome.get(t, {})
        offers = sum(found.get(o, 0) for o in OUTCOMES[3:])
        return percent(offers, sum(found.values())), percent(found.get(OUTCOMES[5], 0), offers)

    (admit, accept), (admit_before, accept_before) = rates(current), rates(before)
    stats = [
        stat(f'Bewerbungen zum {current}', by_term.get(current.pk, 0), before=by_term.get(before.pk) if before else None, series=list(years.values())),
        stat('Zulassungsquote', admit, before=admit_before, unit=' %'),
        stat('Annahmequote', accept, before=accept_before, unit=' %'),
    ]
    if terms[-1] != current:  # a round still open
        running = terms[-1]
        stats.append(stat(f'Bewerbungen zum {running}, laufend', by_term.get(running.pk, 0), before=by_term.get(rounds[-2].pk) if len(rounds) > 1 else None))
    if before:
        subjects = tally(m, ['hauptfach'], [current]), tally(m, ['hauptfach'], [before])
        changes = sorted((percent(n - subjects[1][s], subjects[1][s]), s, n, subjects[1][s]) for s, n in subjects[0].items() if subjects[1].get(s, 0) >= 10)
        names = m.dimensions['hauptfach'].labels([c[1] for c in changes])
        for change, subject, n, was in [changes[0], changes[-1]] if changes else []:
            if abs(change) >= 15:
                insights.append({
                    'text': f'Bewerbungen {names[subject]}: {n} statt {was} zum {before} ({change:+d} %).',
                    'url': Query(m, ['studienjahr'], {'hauptfach': str(subject)}).url(),
                })
    return {
        'title': 'Bewerbung', 'when': f'Runde {current}',
        'stats': stats,
        'charts': [
            chart(Query(m, ['studienjahr'], zeit='j10'), person, 'Bewerbungen je Studienjahr'),
            rated(Query(m, ['hauptfach'], zeit=str(current.pk)), person, 'ergebnis', OUTCOMES[3:], 'zugelassen') | {'title': f'nach Hauptfach, {current}'},
        ],
        'funnel': funnel(current),
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
    return [{'label': label, 'shown': number_format(found[k], 0, force_grouping=True), 'width': percent(found[k], found['all']), 'share': percent(found[k], found['all'])} for label, k in steps] if found['all'] else []


def exams(person, insights):
    m = MEASURES['pruefungen']
    last = semesters(m)[-1:]
    if not last:
        return None
    term = last[0]
    result = tally(m, ['ergebnis'], last)
    count = sum(result.values())
    mean = tally(MEASURES['durchschnitt'], (), last)
    failing = (
        Course.objects.filter(term=term, kind=Course.Kind.EXAM, released__isnull=False)
        .annotate(n=Count('participations', filter=Q(participations__passed__isnull=False)), failed=Count('participations', filter=Q(participations__passed=False)))
        .filter(n__gte=5, failed__gte=F('n') * 0.3).order_by('-failed')
    )
    if failing:
        insights.append({
            'text': f'{plural(len(failing), "Prüfung", "Prüfungen")} mit Durchfallquote ab 30 % im {term}: {", ".join(c.title for c in failing[:2])}{" …" if len(failing) > 2 else ""}.',
            'url': failing[0].get_absolute_url() if 'manage_exams' in person.roles else Query(m, ['studiengang', 'ergebnis'], zeit=str(term.pk), share=True).url(),
        })
    return {
        'title': 'Prüfungen', 'when': term.name,
        'stats': [
            stat('Prüfungsergebnisse', count),
            stat('bestanden', percent(result.get(True, 0), count), unit=' %'),
            stat('Durchschnittsnote', shown=figure(mean, MEASURES['durchschnitt'])),
        ],
        'charts': [
            chart(Query(m, ['note'], zeit=str(term.pk)), person, 'Notenverteilung'),
            rated(Query(m, ['studiengang'], zeit=str(term.pk)), person, 'ergebnis', [True], 'bestanden') | {'title': 'nach Studiengang'},
        ],
    }


def teaching(person, insights):
    term = Term.current()
    courses = Course.objects.filter(term=term, kind=Course.Kind.COURSE).annotate(n=Count('participations'))
    sizes = list(courses.values_list('n', 'capacity'))
    lessons = Course.objects.filter(term=term, kind=Course.Kind.LESSON).count()
    taken = sum(n for n, _ in sizes)
    full = sum(1 for n, cap in sizes if cap and n >= cap)
    small = sum(1 for n, _ in sizes if n < 3)
    m = MEASURES['veranstaltungen']
    query = Query(m, ['groesse'], {'art': Course.Kind.COURSE})
    if small:
        insights.append({'text': f'{plural(small, "Lehrveranstaltung", "Lehrveranstaltungen")} im {term} mit weniger als drei Teilnehmenden.', 'url': query.url()})
    if full:
        insights.append({'text': f'{plural(full, "Lehrveranstaltung ist", "Lehrveranstaltungen sind")} voll belegt, Anmeldungen kommen auf die Warteliste.', 'url': query.url()})
    return {
        'title': 'Lehre', 'when': term.name,
        'stats': [
            stat('Lehrveranstaltungen', len(sizes)),
            stat('Klassen im Einzelunterricht', lessons),
            stat('Belegungen der Lehrveranstaltungen', taken),
            stat('Teilnehmende je Lehrveranstaltung', shown=number_format(taken / len(sizes), 1) if sizes else '–'),
        ],
        'charts': [chart(query, person, 'Lehrveranstaltungen nach Teilnehmenden')],
    }


def rooms(person, insights):
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

    idle = sum(1 for m in by_room.values() if m < 0.1 * open_)
    busy = sum(1 for m in by_room.values() if m > 0.85 * open_)
    query = Query(MEASURES['raumstunden'], ['raum'])
    if idle:
        insights.append({'text': f'{idle} der {len(rooms)} Instituts- und Übungsräume waren in den letzten sieben Tagen zu weniger als 10 % belegt.', 'url': query.url()})
    if busy:
        insights.append({'text': f'{plural(busy, "Raum war", "Räume waren")} zu mehr als 85 % belegt.', 'url': query.url()})
    return {
        'title': 'Räume', 'when': f'{days[0]:%d.%m.} bis {days[-1]:%d.%m.%Y}, {HOURS[0]}–{HOURS[-1] + 1} Uhr',
        'stats': [
            stat('belegt im Durchschnitt', percent(sum(by_room.values()), open_ * len(rooms)), unit=' %'),
            stat('gebuchte Stunden', round(booked)),
            stat('Buchungen von Studierenden', students),
            stat('am vollsten', shown=f'{WEEKDAYS[peak[0].isoweekday()]} {peak[1]}–{peak[1] + 1} Uhr'),
        ],
        'grid': {'hours': list(HOURS), 'days': grid},
        'charts': [
            {'title': 'Belegung nach Raumfunktion', 'bars': bars(lambda r: r.function.name)},
            {'title': 'nach Gebäude', 'bars': bars(lambda r: r.building)},
        ],
    }


def overview(person):
    insights = []
    sections = [students(person, insights), applications(person, insights), exams(person, insights), teaching(person, insights), rooms(person, insights)]
    return {'sections': [s for s in sections if s], 'insights': insights}
