"""Courses and their people into the Hochschule's Moodle through its web services (docs/moodle.md): each published
semester's Lehrveranstaltungen as Moodle courses, under the semester, the Fachgebiet and the Lehrform, their teachers
and participants enrolled, whoever left taken out again, a course called off hidden; a course taught in groups with a
Moodle group for each. ConCampus is the record and Moodle
follows. People are found there by e-mail address; whoever has not signed in to Moodle yet is enrolled the night after
they have. Off without settings.MOODLE."""
import json
import secrets
from collections import Counter
from datetime import datetime, time
from hashlib import sha256
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.html import format_html_join, linebreaks

from .models import Course, MoodleCourse, Participation
from .views import described, usual


class MoodleError(Exception):
    pass


def flat(value, prefix=''):
    """Parameters as Moodle's REST server reads them: courses[0][fullname]=…, booleans as 1 and 0."""
    if isinstance(value, dict | list):
        found = {}
        for key, item in value.items() if isinstance(value, dict) else enumerate(value):
            found |= flat(item, f'{prefix}[{key}]' if prefix else str(key))
        return found
    return {prefix: int(value) if isinstance(value, bool) else value}


def call(function, **params):
    """One function of Moodle's web services."""
    own = settings.MOODLE
    data = urlencode({'wstoken': own['token'], 'wsfunction': function, 'moodlewsrestformat': 'json', **flat(params)}).encode()
    with urlopen(Request(own['url'].rstrip('/') + '/webservice/rest/server.php', data), timeout=60) as response:
        found = json.load(response)
    if isinstance(found, dict) and 'exception' in found:
        raise MoodleError(f'{function}: {found.get("message")}')
    return found


def stamp(day):
    return int(timezone.make_aware(datetime.combine(day, time())).timestamp())


def account(person):
    """The demo's Moodle account for someone: the demo's users sign in with its access code, the others not at all."""
    login = person.username in settings.DEMO and settings.DEMO_CODE
    return {
        'username': person.username.lower(), 'email': person.email, 'firstname': person.first_name, 'lastname': person.last_name,
        'auth': 'manual' if login else 'nologin', 'password': settings.DEMO_CODE if login else secrets.token_urlsafe(),
    }


def summary(course, slot):
    """A course's summary in Moodle, laid out as the Kursinfo teachers write there by hand: who teaches, when and where,
    the places, the modules it counts for, the registration, then its description."""
    rows = {
        'Durchführende*r Dozent*in': ', '.join(map(str, course.staff.all())),
        'Termin': described(slot),
        'Veranstaltungsangebot': course.format and course.format.name,
        'Maximale Anzahl Teilnehmer*innen': course.capacity,
        'Studiengänge/Modulzuordnung': ', '.join(sorted({m.code for m in course.modules.all() if m.code})),
        'Anmeldeschluss': course.deadline and f'{course.deadline:%d.%m.%Y}',
    }
    return format_html_join('', '<p><strong>{}:</strong> {}</p>', ((k, v) for k, v in rows.items() if v)) + (linebreaks(course.description, autoescape=True) if course.description else '')


def grouping(call, course, teams, users, ours):
    """A Moodle course's groups as ConCampus has them, found by their idnumber or made: whoever is missing put in, whoever
    ConCampus enrolled and is in another group now taken out."""
    there = {g['idnumber']: g['id'] for g in call('core_group_get_course_groups', courseid=course)}
    for idnumber, (name, emails) in teams.items():
        if idnumber not in there:
            there[idnumber] = call('core_group_create_groups', groups=[{'courseid': course, 'name': name, 'description': '', 'idnumber': idnumber}])[0]['id']
        group, want = there[idnumber], {users[e] for e in emails if e in users}
        have = set(call('core_group_get_group_members', groupids=[group])[0]['userids'])
        if add := want - have:
            call('core_group_add_group_members', members=[{'groupid': group, 'userid': u} for u in sorted(add)])
        if gone := (have - want) & ours:
            call('core_group_delete_group_members', members=[{'groupid': group, 'userid': u} for u in sorted(gone)])


def sync(call=call, accounts=False, changed=False):
    """Bring Moodle up to ConCampus; returns {what: how many}. With changed, only the courses whose title, place, dates,
    summary or people have changed since ConCampus last sent them. With accounts, as on the demo, where no login of the
    Hochschule makes them, whoever has no account gets one."""
    own, counts, categories = settings.MOODLE, Counter(), {}

    def category(idnumber, name, parent):
        """A category of ConCampus's, found by its idnumber or made."""
        if idnumber not in categories:
            found = call('core_course_get_categories', criteria=[{'key': 'idnumber', 'value': idnumber}], addsubcategories=False)
            if not found:
                found = call('core_course_create_categories', categories=[{'name': name, 'parent': parent, 'idnumber': idnumber}])
                counts['Kategorien angelegt'] += 1
            categories[idnumber] = found[0]['id']
        return categories[idnumber]

    courses = list(
        Course.objects.filter(kind=Course.Kind.COURSE, term__planning=False, term__end__gte=timezone.localdate())
        .filter(Q(cancelled=None) | Q(moodle__isnull=False)).select_related('term', 'area', 'format')
        .prefetch_related('staff', 'modules', Prefetch('participations', Participation.objects.select_related('study__person')), 'groups__staff').order_by('term__start', 'pk')
    )
    mirrors = {m.course_id: m for m in MoodleCourse.objects.filter(course__in=courses)}
    wanted = {c.pk: {p.study.person.email.lower(): own['student'] for p in c.participations.all()} | {t.email.lower(): own['teacher'] for t in c.staff.all()} for c in courses}
    teams = {c.pk: {  # each group: its name and people
        f'campus-group-{g.pk}': (f'Gruppe {g.number}', sorted({p.study.person.email.lower() for p in c.participations.all() if p.group_id == g.pk} | {t.email.lower() for t in g.staff.all()}))
        for g in c.groups.all()
    } for c in courses}
    slots, todo = usual(courses), []
    for course in courses:
        term = course.term
        fields = {
            'fullname': course.title, 'shortname': f'{course.title} ({term.name}, {course.pk})'[:255], 'idnumber': f'campus-course-{course.pk}',
            'summary': summary(course, slots.get(course.pk)), 'summaryformat': 1,
            'startdate': stamp(term.lectures_start), 'enddate': stamp(term.end), 'visible': course.cancelled is None,
        }
        place = [term.name, course.area_id and course.area.name, course.format_id and course.format.name]
        state = sha256(json.dumps([fields, place, wanted[course.pk], teams[course.pk]], sort_keys=True).encode()).hexdigest()
        if not (changed and course.pk in mirrors and mirrors[course.pk].state == state):
            todo.append((course, fields, state))
    emails = sorted({e for course, _, _ in todo for e in wanted[course.pk] if e})
    users = {}
    for i in range(0, len(emails), 50):
        users |= {u['email'].lower(): u['id'] for u in call('core_user_get_users_by_field', field='email', values=emails[i:i + 50])}
    if accounts:
        people = {p.email.lower(): p for c, _, _ in todo for p in [*c.staff.all(), *(q.study.person for q in c.participations.all())]}
        new = [people[e] for e in emails if e not in users]
        for i in range(0, len(new), 50):
            made = {u['username']: u['id'] for u in call('core_user_create_users', users=[account(p) for p in new[i:i + 50]])}
            users |= {p.email.lower(): made[p.username.lower()] for p in new[i:i + 50]}
        counts['Konten angelegt'] += len(new)
    for course, fields, state in todo:
        term = course.term
        parent = category(f'campus-{term.pk}', term.name.replace('WiSe', 'Wintersemester').replace('SoSe', 'Sommersemester'), own['category'])
        if course.area:
            parent = category(f'campus-{term.pk}-{course.area_id}', course.area.name, parent)
        if course.format:
            parent = category(f'campus-{term.pk}-{course.area_id or 0}-{course.format_id}', course.format.name, parent)
        fields['categoryid'] = parent
        if not (mirror := mirrors.get(course.pk)):  # one made before, its link here lost, is taken up again
            if there := call('core_course_get_courses_by_field', field='idnumber', value=fields['idnumber'])['courses']:
                mirror = MoodleCourse.objects.create(course=course, moodle_id=there[0]['id'])
            else:
                mirror = MoodleCourse.objects.create(course=course, moodle_id=call('core_course_create_courses', courses=[fields])[0]['id'])
                counts['Kurse angelegt'] += 1
        call('core_course_update_courses', courses=[{'id': mirror.moodle_id, **fields}])
        if not course.cancelled:  # one called off is hidden, whoever is in keeps what is there
            roles = {users[e]: role for e, role in wanted[course.pk].items() if e in users}
            if roles:  # enrolling again changes nothing for whoever is in already
                call('enrol_manual_enrol_users', enrolments=[{'roleid': role, 'userid': user, 'courseid': mirror.moodle_id} for user, role in roles.items()])
            if gone := set(mirror.people) - set(roles):
                call('enrol_manual_unenrol_users', enrolments=[{'userid': user, 'courseid': mirror.moodle_id} for user in sorted(gone)])
            counts['eingeschrieben'] += len(set(roles) - set(mirror.people))
            counts['ausgetragen'] += len(gone)
            mirror.people = sorted(roles)
            if teams[course.pk]:
                grouping(call, mirror.moodle_id, teams[course.pk], users, set(roles))
        mirror.synced, mirror.state = timezone.now(), state
        mirror.save(update_fields=['people', 'synced', 'state'])
    counts['Kurse'], counts['ohne Moodle-Konto'] = len(todo), len(set(emails) - set(users))
    return counts
