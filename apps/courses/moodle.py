"""Courses and their people into the Hochschule's Moodle through its web services (docs/moodle.md): each published
semester's Lehrveranstaltungen marked „In Moodle“ as Moodle courses, under the semester, the Fachgebiet and the
Lehrform, with the material of the course they were taken over from; their teachers and participants enrolled, whoever
left taken out again, a course called off hidden; a course taught in groups with a Moodle group for each. ConCampus is
the record and Moodle follows. People are found there by their Hochschule login, else by e-mail address; whoever has not
signed in to Moodle yet is enrolled within a minute of their first sign-in. Off without settings.MOODLE."""
import hmac
import json
import secrets
import time as clock
from collections import Counter
from datetime import datetime, time
from hashlib import sha256
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.html import format_html, format_html_join, linebreaks

from core.views import demo_people
from .models import Course, MoodleCourse, Participation
from .views.rollover import described, usual


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


class Probe:
    """Moodle for a trial run (manage.py moodle --probe): read as ever, nothing written; what would be made gets a
    made-up id above any real one, which is never looked up there."""
    WRITES = {
        'core_course_create_categories', 'core_course_create_courses', 'core_course_update_courses', 'core_user_create_users', 'enrol_manual_enrol_users',
        'enrol_manual_unenrol_users', 'core_group_create_groups', 'core_group_add_group_members', 'core_group_delete_group_members', 'core_course_import_course',
    }
    MADE = 2_000_000_000

    def __init__(self):
        self.made = self.MADE

    def __call__(self, function, **params):
        if function == 'core_group_get_course_groups' and params['courseid'] >= self.MADE:
            return []
        if function == 'core_group_get_group_members' and params['groupids'][0] >= self.MADE:
            return [{'groupid': params['groupids'][0], 'userids': []}]
        if function not in self.WRITES:
            return call(function, **params)
        made = params.get('categories') or params.get('courses') or params.get('groups') or params.get('users') or []
        if function in ('core_course_create_categories', 'core_course_create_courses', 'core_group_create_groups', 'core_user_create_users'):
            return [{'id': self.new(), 'username': m.get('username')} for m in made]
        return None

    def new(self):
        self.made += 1
        return self.made


def stamp(day):
    return int(timezone.make_aware(datetime.combine(day, time())).timestamp())


def account(person):
    """The demo's Moodle account for someone: signed in only through ConCampus (entry), never by password."""
    return {
        'username': person.username.lower(), 'email': person.email, 'firstname': person.first_name, 'lastname': person.last_name,
        'auth': 'concampus', 'password': secrets.token_urlsafe(),
    }


def entry(username):
    """The address that signs `username` in to the demo's Moodle for the next minute (demo-moodle/concampus/login.php),
    signed with the web service token, which both sides know."""
    own, now = settings.MOODLE, int(clock.time())
    signature = hmac.new(own['token'].encode(), f'{username.lower()}|{now}'.encode(), sha256).hexdigest()
    return own['url'].rstrip('/') + '/auth/concampus/login.php?' + urlencode({'u': username.lower(), 't': now, 's': signature})


def addresses(courses):
    """{course pk: its address in Moodle}, of those there."""
    url = settings.MOODLE['url'].rstrip('/')
    return {c: f'{url}/course/view.php?id={m}' for c, m in MoodleCourse.objects.filter(course__in=courses).values_list('course', 'moodle_id')} if url else {}


def place(course):
    """A course's place in Moodle for its page: (its address there, '') or ('', when it comes); None where it never goes."""
    if not settings.MOODLE['url'] or course.kind != Course.Kind.COURSE or not course.in_moodle or course.cancelled or course.term.end < timezone.localdate():
        return None
    if url := addresses([course.pk]).get(course.pk):
        return url, ''
    return '', 'mit der Veröffentlichung des Semesters' if course.term.planning else 'in wenigen Minuten'


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
        'Anmeldung und Teilnehmende': settings.ALLOWED_HOSTS and format_html('<a href="https://{}{}">in ConCampus</a>', settings.ALLOWED_HOSTS[0], course.get_absolute_url()),
    }
    return format_html_join('', '<p><strong>{}:</strong> {}</p>', ((k, v) for k, v in rows.items() if v)) + (linebreaks(course.description, autoescape=True) if course.description else '')


def chunks(values, size=50):
    return (values[i:i + size] for i in range(0, len(values), size))


def found(call, people):
    """{person pk: Moodle user id} of those with a Moodle account: by their Hochschule login, Moodle's username where both
    sign in through the Hochschule, else by e-mail address, which may differ between the two."""
    users, logins = {}, {p.sso_id.lower(): p.pk for p in people if p.sso_id}
    for values in chunks(sorted(logins)):
        users |= {logins[u['username']]: u['id'] for u in call('core_user_get_users_by_field', field='username', values=values) if u['username'] in logins}
    emails = {p.email.lower(): p.pk for p in people if p.pk not in users and p.email}
    for values in chunks(sorted(emails)):
        users |= {emails[u['email'].lower()]: u['id'] for u in call('core_user_get_users_by_field', field='email', values=values) if u['email'].lower() in emails}
    return users


def grouping(call, course, teams, users, ours):
    """A Moodle course's groups as ConCampus has them, found by their idnumber or made: whoever is missing put in, whoever
    ConCampus enrolled and is in another group now taken out."""
    there = {g['idnumber']: g['id'] for g in call('core_group_get_course_groups', courseid=course)}
    for idnumber, (name, members) in teams.items():
        if idnumber not in there:
            there[idnumber] = call('core_group_create_groups', groups=[{'courseid': course, 'name': name, 'description': '', 'idnumber': idnumber}])[0]['id']
        group, want = there[idnumber], {users[pk] for pk in members if pk in users}
        have = set(call('core_group_get_group_members', groupids=[group])[0]['userids'])
        if add := want - have:
            call('core_group_add_group_members', members=[{'groupid': group, 'userid': u} for u in sorted(add)])
        if gone := (have - want) & ours:
            call('core_group_delete_group_members', members=[{'groupid': group, 'userid': u} for u in sorted(gone)])


def sync(call=call, accounts=False, changed=False):
    """Bring Moodle up to ConCampus; returns {what: how many}. With changed, only the courses whose title, place, dates,
    summary or people have changed since ConCampus last sent them. With accounts, as on the demo, where no login of the
    Hochschule makes them, whoever has no account gets one, the demo's people too."""
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
        .filter(Q(cancelled=None, in_moodle=True) | Q(moodle__isnull=False)).select_related('term', 'area', 'format')
        .prefetch_related('staff', 'modules', Prefetch('participations', Participation.objects.select_related('study__person')), 'groups__staff').order_by('term__start', 'pk')
    )
    mirrors = {m.course_id: m for m in MoodleCourse.objects.filter(course__in=courses)}
    sources = dict(MoodleCourse.objects.filter(course__in={c.origin_id for c in courses if c.origin_id}).values_list('course', 'moodle_id'))
    people = {t.pk: t for c in courses for t in c.staff.all()} | {p.study.person_id: p.study.person for c in courses for p in c.participations.all()}
    waiting = {c.pk: c.waitlist() for c in courses}  # who waits for a place is not in the course yet
    wanted = {
        c.pk: {p.study.person_id: own['student'] for p in c.participations.all() if p.pk not in waiting[c.pk]} | {t.pk: own['teacher'] for t in c.staff.all()}
        for c in courses
    }
    teams = {c.pk: {  # each group: its name and people
        f'campus-group-{g.pk}': (f'Gruppe {g.number}', sorted({p.study.person_id for p in c.participations.all() if p.group_id == g.pk and p.pk not in waiting[c.pk]} | {t.pk for t in g.staff.all()}))
        for g in c.groups.all()
    } for c in courses}
    # each minute, whoever had no Moodle account: once they have one, their courses are sent again and they are enrolled
    arrived = set(found(call, [people[pk] for m in mirrors.values() for pk in m.absent if pk in people])) if changed else set()
    slots, todo = usual(courses), []
    for course in courses:
        term = course.term
        fields = {
            'fullname': course.title, 'shortname': f'{course.title} ({term.name}, {course.pk})'[:255], 'idnumber': f'campus-course-{course.pk}',
            'summary': summary(course, slots.get(course.pk)), 'summaryformat': 1,
            'startdate': stamp(term.lectures_start), 'enddate': stamp(term.end), 'visible': course.cancelled is None and course.in_moodle,
        }
        place = [term.name, course.area_id and course.area.name, course.format_id and course.format.name]
        state = sha256(json.dumps([fields, place, wanted[course.pk], teams[course.pk]], sort_keys=True).encode()).hexdigest()
        mirror = mirrors.get(course.pk)
        if not (changed and mirror and mirror.state == state and not arrived & set(mirror.absent)):
            todo.append((course, fields, state))
    demo = [p for _, shown in demo_people() for p, _ in shown] if accounts else []  # each can sign in, in a course or not
    asked = {pk: people[pk] for course, _, _ in todo for pk in wanted[course.pk]} | {p.pk: p for p in demo}
    users = found(call, asked.values())
    if accounts:
        new = [p for pk, p in sorted(asked.items()) if pk not in users and p.email]
        for chunk in chunks(new):
            made = {u['username']: u['id'] for u in call('core_user_create_users', users=[account(p) for p in chunk])}
            users |= {p.pk: made[p.username.lower()] for p in chunk}
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
                if course.copy_content and (source := sources.get(course.origin_id)):  # last time's material, once
                    call('core_course_import_course', importfrom=source, importto=mirror.moodle_id, deletecontent=0)
                    counts['Inhalte übernommen'] += 1
        call('core_course_update_courses', courses=[{'id': mirror.moodle_id, **fields}])
        if fields['visible']:  # one called off or taken out of Moodle is hidden, whoever is in keeps what is there
            roles = {users[pk]: role for pk, role in wanted[course.pk].items() if pk in users}
            if roles:  # enrolling again changes nothing for whoever is in already
                call('enrol_manual_enrol_users', enrolments=[{'roleid': role, 'userid': user, 'courseid': mirror.moodle_id} for user, role in roles.items()])
            if gone := set(mirror.people) - set(roles):
                call('enrol_manual_unenrol_users', enrolments=[{'userid': user, 'courseid': mirror.moodle_id} for user in sorted(gone)])
            counts['eingeschrieben'] += len(set(roles) - set(mirror.people))
            counts['ausgetragen'] += len(gone)
            mirror.people, mirror.absent = sorted(roles), sorted(pk for pk in wanted[course.pk] if pk not in users)
            if teams[course.pk]:
                grouping(call, mirror.moodle_id, teams[course.pk], users, set(roles))
        mirror.synced, mirror.state = timezone.now(), state
        mirror.save(update_fields=['people', 'absent', 'synced', 'state'])
    counts['Kurse'], counts['ohne Moodle-Konto'] = len(todo), len(set(asked) - set(users))
    return counts
