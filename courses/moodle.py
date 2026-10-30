"""Courses and their people into the Hochschule's Moodle through its web services (docs/moodle.md): each published
semester's Lehrveranstaltungen as Moodle courses, under the semester, the Fachgebiet and the Lehrform, their teachers
and participants enrolled, whoever left taken out again, a course called off hidden. Campus is the record and Moodle
follows. People are found there by e-mail address; whoever has not signed in to Moodle yet is enrolled the night after
they have. Off without settings.MOODLE."""
import json
from collections import Counter
from datetime import datetime, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.models import Prefetch, Q
from django.utils import timezone

from .models import Course, MoodleCourse, Participation


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


def sync(call=call):
    """Bring Moodle up to Campus; returns {what: how many}."""
    own, counts, categories = settings.MOODLE, Counter(), {}

    def category(idnumber, name, parent):
        """A category of Campus's, found by its idnumber or made."""
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
        .prefetch_related('staff', Prefetch('participations', Participation.objects.select_related('study__person'))).order_by('term__start', 'pk')
    )
    mirrors = {m.course_id: m for m in MoodleCourse.objects.filter(course__in=courses)}
    wanted = {c.pk: {p.study.person.email.lower(): own['student'] for p in c.participations.all()} | {t.email.lower(): own['teacher'] for t in c.staff.all()} for c in courses}
    emails = sorted({e for people in wanted.values() for e in people if e})
    users = {}
    for i in range(0, len(emails), 50):
        users |= {u['email'].lower(): u['id'] for u in call('core_user_get_users_by_field', field='email', values=emails[i:i + 50])}
    for course in courses:
        term = course.term
        parent = category(f'campus-{term.pk}', term.name.replace('WiSe', 'Wintersemester').replace('SoSe', 'Sommersemester'), own['category'])
        if course.area:
            parent = category(f'campus-{term.pk}-{course.area_id}', course.area.name, parent)
        if course.format:
            parent = category(f'campus-{term.pk}-{course.area_id or 0}-{course.format_id}', course.format.name, parent)
        fields = {
            'fullname': course.title, 'shortname': f'{course.title} ({term.name}, {course.pk})'[:255], 'categoryid': parent,
            'idnumber': f'campus-course-{course.pk}', 'summary': course.description, 'summaryformat': 2,  # plain text
            'startdate': stamp(term.lectures_start), 'enddate': stamp(term.end), 'visible': course.cancelled is None,
        }
        if mirror := mirrors.get(course.pk):
            call('core_course_update_courses', courses=[{'id': mirror.moodle_id, **fields}])
        else:
            mirror = MoodleCourse.objects.create(course=course, moodle_id=call('core_course_create_courses', courses=[fields])[0]['id'])
            counts['Kurse angelegt'] += 1
        if course.cancelled:  # hidden; whoever is in keeps what is there
            continue
        roles = {users[e]: role for e, role in wanted[course.pk].items() if e in users}
        if roles:  # enrolling again changes nothing for whoever is in already
            call('enrol_manual_enrol_users', enrolments=[{'roleid': role, 'userid': user, 'courseid': mirror.moodle_id} for user, role in roles.items()])
        if gone := set(mirror.people) - set(roles):
            call('enrol_manual_unenrol_users', enrolments=[{'userid': user, 'courseid': mirror.moodle_id} for user in sorted(gone)])
        counts['eingeschrieben'] += len(set(roles) - set(mirror.people))
        counts['ausgetragen'] += len(gone)
        mirror.people, mirror.synced = sorted(roles), timezone.now()
        mirror.save(update_fields=['people', 'synced'])
    counts['Kurse'], counts['ohne Moodle-Konto'] = len(courses), len(set(emails) - set(users))
    return counts
