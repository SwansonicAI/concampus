from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Count
from django.test import Client
from django.utils import timezone

from core.models import Holiday, Message, Person, Term
from curriculum.models import Program
from rooms.models import Room
from rooms.views import agenda
from core.testing import Seeded, flashes
from students.models import Study
from .models import Course, Ensemble, MoodleCourse, Participation, Signup, owed_module
from .moodle import flat, sync
from .views import deputat, loads, taking


class RolloverTest(Seeded):
    def test_next_term_is_planned_unseen_then_published(self):
        referat, student = self.login('dekanatsreferat'), self.login('student')
        rows = referat.get('/lehre/semesterwechsel/').context['rows']
        once = [c for c in rows if c.rhythm == 'once']
        kept, *taken = [c for c in rows if c.rhythm != 'once']  # one left out, e.g. for a sabbatical
        r = referat.post('/lehre/semesterwechsel/', {'schritt': 'uebernehmen', 'course': [c.pk for c in taken]}, follow=True)
        self.assertEqual(flashes(r), [f'{len(taken)} Veranstaltungen nach SoSe 2027 übernommen als Entwurf; Studierende sehen sie, sobald das Semester veröffentlicht ist.'])
        self.assertFalse(Course.objects.filter(term__name='SoSe 2027', title=kept.title).exists())
        self.assertTrue(once)  # offered, not ticked
        self.assertFalse(Participation.objects.filter(course__term__name='SoSe 2027', course__kind='lesson', course__origin__isnull=False).exists())  # classes fill after the Rückmeldung
        draft = Course.objects.filter(term__name='SoSe 2027', kind='course').first()
        self.assertEqual(student.get(draft.get_absolute_url()).status_code, 404)
        teacher = Client()
        teacher.force_login(draft.staff.first())
        teacher.post(f'/lehre/{draft.pk}/rueckmeldung/', {'antwort': 'entfaellt', 'grund': 'Forschungssemester'})
        self.assertEqual(referat.post('/lehre/semesterwechsel/', {'schritt': 'veroeffentlichen'}).status_code, 403)  # the Dekanat's
        self.login('dekanat').post('/lehre/semesterwechsel/', {'schritt': 'veroeffentlichen'})
        self.assertFalse(Course.objects.filter(pk=draft.pk).exists())  # called off before anyone saw it
        shown = Course.objects.filter(term__name='SoSe 2027', kind='course').first()
        self.assertEqual(student.get(shown.get_absolute_url()).status_code, 200)
        self.assertEqual(student.post('/lehre/semesterwechsel/').status_code, 403)


class PlacesTest(Seeded):
    def test_teachers_choose_or_assign(self):
        signup = Signup.objects.filter(study__person__username='student').select_related('course', 'study').first()
        course, study = signup.course, signup.study
        self.assertEqual(self.login('student').get(course.get_absolute_url()).context['wished'], signup)
        teacher = Client()
        teacher.force_login(course.staff.first())
        teacher.post(f'/lehre/anfrage/{signup.pk}/', {'decision': 'yes'})
        self.assertTrue(Participation.objects.filter(course=course, study=study).exists())
        self.assertFalse(Signup.objects.filter(pk=signup.pk).exists())
        lesson = Course.objects.filter(term__name='WiSe 2026/27', allocation='staff', modules__program=study.program).exclude(participations__study=study).first()
        r = self.login('student').post(f'/lehre/{lesson.pk}/anmelden/', {'study': study.pk}, follow=True)
        self.assertEqual(flashes(r), ['Die Lehrenden teilen die Plätze ein.'])


class WaitlistTest(Seeded):
    def test_the_waitlist_moves_up_and_teachers_write_to_their_participants(self):
        course = Course.objects.filter(term__name='WiSe 2026/27', kind='course', allocation='order').annotate(n=Count('participations')).filter(n__gte=4).first()
        rows = list(course.participations.order_by('created', 'pk').select_related('study__person'))
        course.capacity, course.deadline = len(rows) - 2, timezone.localdate() + timedelta(days=7)
        course.save()
        self.assertEqual(course.waitlist(), {rows[-2].pk: 1, rows[-1].pk: 2})

        def moved(p):
            return Message.objects.filter(person=p.study.person, subject=f'Platz frei: {course.title}').exists()

        student = Client()
        student.force_login(rows[0].study.person)
        student.post(f'/lehre/teilnahme/{rows[0].pk}/abmelden/')
        self.assertEqual((moved(rows[-2]), moved(rows[-1])), (True, False))
        teacher = Client()
        teacher.force_login(course.staff.first())
        teacher.post(f'/lehre/{course.pk}/austragen/', {'study': [rows[1].study_id]})
        self.assertEqual((course.participations.filter(pk=rows[1].pk).exists(), moved(rows[-1]), course.waitlist()), (False, True, {}))
        teacher.post(f'/lehre/{course.pk}/nachricht/', {'study': [rows[2].study_id], 'subject': 'Raum', 'body': 'Hallo {vorname}'})
        self.assertEqual(list(Message.objects.filter(subject='Raum').values_list('person', 'body')), [(rows[2].study.person_id, f'Hallo {rows[2].study.person.first_name}')])
        self.assertEqual(self.login('student').get(f'/lehre/{course.pk}/nachricht/').status_code, 403)


class GroupTest(Seeded):
    def test_one_place_a_person_none_past_a_groups_last_and_a_switch_keeps_it(self):
        course = Course.objects.filter(term__name='WiSe 2026/27', groups__isnull=False).distinct().first()
        course.deadline = timezone.localdate() + timedelta(days=7)
        course.save()
        study = next(s for s in Study.objects.filter(status='enrolled', program__modules__courses=course).exclude(person__studies__participations__course=course).distinct() if owed_module(course, s))
        full = next(g for g in course.groups.all() if taking(g) >= g.capacity)
        first, second = [g for g in course.groups.all() if taking(g) < g.capacity][:2]
        student = Client()
        student.force_login(study.person)

        def register(group):
            return flashes(student.post(f'/lehre/{course.pk}/anmelden/', {'study': study.pk, 'gruppe': group.pk}, follow=True))

        self.assertEqual(register(full), [f'Gruppe {full.number} ist inzwischen voll. Bitte eine andere wählen.'])
        self.assertEqual(register(first), [f'Sie sind angemeldet, in Gruppe {first.number}.'])
        self.assertEqual(register(second), [f'Sie haben schon einen Platz in Gruppe {first.number}. Die Gruppe wechseln Sie oben unter „Meine Anmeldung“.'])
        place = Participation.objects.get(course=course, study=study)
        student.post(f'/lehre/teilnahme/{place.pk}/wechseln/', {'gruppe': full.pk})
        place.refresh_from_db()
        self.assertEqual(place.group, first)  # full: the place stays
        student.post(f'/lehre/teilnahme/{place.pk}/wechseln/', {'gruppe': second.pk})
        place.refresh_from_db()
        self.assertEqual(place.group, second)
        self.assertEqual(set(agenda(study.person).filter(course=course).values_list('group', flat=True)), {second.pk})  # its own group's dates only
        teacher = second.staff.first()
        self.assertEqual(deputat(Course.objects.filter(pk=course.pk), teacher), course.hours * course.groups.filter(staff=teacher).count())

    def test_moodle_has_a_group_for_each_and_follows_a_move(self):
        course = Course.objects.filter(term__name='WiSe 2026/27', groups__isnull=False).distinct().first()
        group, other = course.groups.all()[:2]
        person = group.participations.select_related('study__person').first().study.person
        moodle = FakeMoodle([person.email, *(t.email for t in course.staff.all())])
        sync(moodle)
        mine = MoodleCourse.objects.get(course=course).moodle_id
        there = {g['idnumber']: n for n, g in moodle.groups.items() if g['courseid'] == mine}
        user = moodle.users[person.email.lower()]
        self.assertEqual((len(there), user in moodle.members[there[f'campus-group-{group.pk}']]), (course.groups.count(), True))
        Participation.objects.filter(course=course, study__person=person).update(group=other)
        sync(moodle, changed=True)
        self.assertEqual((user in moodle.members[there[f'campus-group-{group.pk}']], user in moodle.members[there[f'campus-group-{other.pk}']]), (False, True))


class SettledTest(Seeded):
    def test_a_wahlkatalog_with_its_credits_earned_takes_no_more(self):
        study = Study.objects.get(person__username='student')
        course = Course.objects.get(term__name='WiSe 2026/27', title='Mentales Training')
        self.assertTrue(course.modules.filter(program=study.program).exclude(participations__study=study, participations__passed=True).exists())
        self.assertIsNone(owed_module(course, study))
        listed = self.login('student').get('/lehre/verzeichnis/').context['courses']
        self.assertNotIn(course, listed)


class EnsembleTest(Seeded):
    def test_register_coach_and_count_the_load(self):
        chamber = Course.objects.get(term__name='WiSe 2026/27', title='Kammermusik')
        chamber.opens = chamber.deadline = None
        chamber.save()
        cellist = Study.objects.filter(status='enrolled', subject__name='Violoncello', program__modules__courses=chamber).exclude(person__username='student').first()
        student = Study.objects.get(person__username='student')
        r = self.login('student').post(f'/lehre/{chamber.pk}/ensemble/', {
            'study': student.pk, 'name': 'Duo Violine/Violoncello', 'members': [cellist.person_id], 'repertoire': 'Ravel: Sonate',
        }, follow=True)
        self.assertEqual(flashes(r), ['„Duo Violine/Violoncello“ angemeldet. Wer es betreut, erfahren Sie im Postfach.'])
        duo = Ensemble.objects.get(name='Duo Violine/Violoncello')
        self.assertEqual(set(duo.members.all()), {student, cellist})
        self.assertTrue(chamber.participations.filter(study=cellist).exists())
        wendt = Person.objects.get(username='lehre')
        before = deputat(wendt.teaching.filter(term=chamber.term), wendt)
        self.login('lehre').post(f'/lehre/ensemble/{duo.pk}/', {'coach': wendt.pk})
        self.assertEqual(deputat(wendt.teaching.filter(term=chamber.term), wendt), before + chamber.hours)

    def test_a_member_takes_the_ensemble_back_while_registration_runs(self):
        chamber = Course.objects.get(term__name='WiSe 2026/27', title='Kammermusik')
        chamber.opens = chamber.deadline = None
        chamber.save()
        cellist = Study.objects.filter(status='enrolled', subject__name='Violoncello', program__modules__courses=chamber, ensembles=None).exclude(person__username='student').first()
        student = self.login('student')
        student.post(f'/lehre/{chamber.pk}/ensemble/', {'study': Study.objects.get(person__username='student').pk, 'name': 'Duo', 'members': [cellist.person_id]})
        page = student.get(f'/lehre/{chamber.pk}/')
        self.assertEqual((page.context['count'] > 1, 'Ensemble anmelden' in page.content.decode()), (True, False))  # in one: everyone counted, no second form
        duo = Ensemble.objects.get(name='Duo')
        self.login(cellist.person.username).post(f'/lehre/ensemble/{duo.pk}/zurueckziehen/')
        self.assertFalse(Ensemble.objects.filter(pk=duo.pk).exists() or chamber.participations.filter(study=cellist).exists())

    def test_a_coach_grades_their_own_ensembles(self):
        chamber = Course.objects.get(term__name='WiSe 2026/27', title='Kammermusik')
        rows = self.login('lehre').get(f'/lehre/{chamber.pk}/').context['rows']
        coached = set(Study.objects.filter(ensembles__course=chamber, ensembles__coach__username='lehre').values_list('pk', flat=True))
        self.assertEqual({p.study_id for p in rows}, coached)

    def test_teachers_take_open_ensembles_the_office_hands_out_the_rest(self):
        chamber = Course.objects.get(term__name='WiSe 2026/27', title='Kammermusik')
        wendt, teacher = Person.objects.get(username='lehre'), self.login('lehre')
        others = chamber.ensembles.exclude(coach=None).exclude(coach=wendt).first()
        self.assertEqual(teacher.post(f'/lehre/ensemble/{others.pk}/', {'coach': wendt.pk}).status_code, 403)
        self.assertEqual(teacher.post(f'/lehre/ensemble/{others.pk}/', {'dissolve': '1'}).status_code, 403)
        self.login('dekanatsreferat').post(f'/lehre/ensemble/{others.pk}/', {'coach': wendt.pk})
        self.assertEqual(Ensemble.objects.get(pk=others.pk).coach, wendt)


class FocusTest(Seeded):
    def test_a_course_of_a_schwerpunkt_not_chosen_says_so(self):
        mei = Study.objects.get(person__username='student')
        options = mei.program.chosen_by()
        course = next(c for c in Course.objects.filter(term__name='WiSe 2026/27', kind='course', modules__in=list(options)).distinct()
                      if {options.get(m.pk) for m in c.modules.filter(program=mei.program)} - {None} and None not in {options.get(m.pk) for m in c.modules.filter(program=mei.program)})
        course.opens, course.deadline, course.allocation = None, None, 'order'
        course.save()
        r = self.login('student').post(f'/lehre/{course.pk}/anmelden/', {'study': mei.pk}, follow=True)
        self.assertIn('noch nicht gewählt', flashes(r)[0])


class TeachingTest(Seeded):
    def test_teachers_change_what_is_theirs_not_hours_staff_or_modules(self):
        course = Course.objects.get(title='Hauptfach Violine · Klasse Wendt', term__name='WiSe 2026/27')
        hours, staff = course.hours, set(course.staff.all())
        self.login('lehre').post(f'/lehre/{course.pk}/bearbeiten/', {
            'hours': '9', 'staff': [Person.objects.get(username='dekanat').pk], 'title': 'Anders', 'capacity': '12', 'allocation': 'staff', 'description': 'Neu',
        })
        course.refresh_from_db()
        self.assertEqual((course.hours, set(course.staff.all()), course.title, course.capacity), (hours, staff, 'Hauptfach Violine · Klasse Wendt', 12))

    def test_hours_and_modules_are_planned_by_the_office_that_plans_teaching(self):
        course = Course.objects.filter(kind='course', term__name='WiSe 2026/27', staff__institute__name='Streichinstrumente und Harfe').first()
        form = self.login('institut').get(f'/lehre/{course.pk}/bearbeiten/').context['form']
        self.assertFalse({'hours', 'modules', 'format', 'kind'} & set(form.fields))
        self.assertTrue({'hours', 'modules', 'format', 'kind'} <= set(self.login('dekanatsreferat').get(f'/lehre/{course.pk}/bearbeiten/').context['form'].fields))

    def test_the_deputat_counts_against_the_obligation_less_reductions(self):
        wendt = Person.objects.get(username='lehre')
        row = next(r for r in loads(Term.objects.get(name='WiSe 2026/27'), wendt) if r.teacher == wendt)
        self.assertEqual((row.due, row.balance, row.account), (wendt.obligation - 2, row.total - row.due, row.total - row.due))  # the account starts this term


class PlanTest(Seeded):
    def test_weekly_dates_leave_out_the_lecture_free_days(self):
        term, dekanat = Term.objects.get(name='WiSe 2026/27'), Person.objects.get(username='dekanat')
        course = Course.objects.create(term=term, title='Samstagsseminar')
        course.staff.add(dekanat)
        self.login('dekanatsreferat').post(f'/lehre/{course.pk}/planen/', {'weekday': 5, 'time': '08:00', 'minutes': 60, 'room': Room.objects.get(name='HzH 313').pk})
        days = {timezone.localtime(b.start).date() for b in course.bookings.all()}
        self.assertTrue(days)
        self.assertFalse(days & Holiday.days(term.lectures_start, term.lectures_end))  # Reformationstag and the Christmas break fall on Saturdays

    def test_a_student_gets_a_new_weekly_lesson_and_hears_of_a_moved_one(self):
        mei, course = Person.objects.get(username='student'), Course.objects.get(title='Hauptfach Violine · Klasse Wendt', term__name='WiSe 2026/27')
        teacher = self.login('lehre')
        rows = teacher.get(f'/lehre/{course.pk}/').context['lessons']
        data = {'form-TOTAL_FORMS': len(rows.forms), 'form-INITIAL_FORMS': len(rows.forms)}
        for n, form in enumerate(rows.forms):
            row = {**form.initial, **({'weekday': 5, 'time': '20:00', 'minutes': 90} if form.initial['person'] == mei.pk else {})}
            data |= {f'form-{n}-{name}': '' if value is None else f'{value:%H:%M}' if hasattr(value, 'hour') else value for name, value in row.items()}
        teacher.post(f'/lehre/{course.pk}/unterrichtszeiten/', data)
        lessons = course.bookings.filter(participants=mei, start__gte=timezone.now())
        self.assertTrue(lessons)
        self.assertEqual({(timezone.localtime(b.start).weekday(), f'{timezone.localtime(b.start):%H:%M}') for b in lessons}, {(5, '20:00')})
        self.assertTrue(mei.messages.filter(subject=f'Unterrichtszeit: {course.title}').exists())

        first = lessons.first()
        r = teacher.post(f'/raeume/buchung/{first.pk}/absagen/', {'day': f'{first.start + timedelta(days=1):%d.%m.%Y}', 'begin': '20:00', 'until': '21:30'}, follow=True)
        self.assertIn('Verlegt', flashes(r)[-1])
        first.refresh_from_db()
        self.assertEqual(first.category_id, 'abgesagt')
        self.assertTrue(course.bookings.filter(participants=mei, start=first.start + timedelta(days=1)).exists())
        self.assertTrue(mei.messages.filter(subject=f'Verlegt: {course.title}').exists())


class IntakeTest(Seeded):
    def test_an_intake_comes_again_a_year_on_in_the_po_version_then_in_force(self):
        """A Studiengang copied as a new PO-Version from next winter; last winter's Eignungsprüfungen into that winter in
        one go: each stage with its jury and the stage it follows, its Fristen a year on, in the new PO-Version."""
        winter = Term.objects.get(name='WiSe 2026/27')
        coming = Term.objects.create(
            name='WiSe 2027/28', start=date(2027, 10, 1), end=date(2028, 3, 31), lectures_start=date(2027, 10, 11), lectures_end=date(2028, 2, 5),
            reregistration_start=date(2027, 7, 1), deadline=date(2027, 8, 31), fee=100,
        )
        intake = Course.objects.filter(term=winter, kind='audition', program__isnull=False).exclude(following=None).exclude(staff=None).first()
        self.login('dekanatsreferat').post(f'/studiengaenge/{intake.program.pk}/kopieren/', {'gilt_ab': coming.pk})
        version = Program.objects.get(degree=intake.program.degree, name=intake.program.name, starts=coming)
        self.assertEqual(version.modules.count(), intake.program.modules.count())
        self.assertEqual(Program.current(winter)[0].starts, None)  # who starts before then starts in the old one
        self.login('asa').post('/lehre/eignungspruefungen/uebernehmen/')
        copy = Course.objects.get(term=coming, title=intake.title)
        self.assertEqual((copy.program, set(copy.staff.all()), copy.origin), (version, set(intake.staff.all()), intake))
        self.assertEqual({c.title for c in copy.following.all()}, {c.title for c in intake.following.all()})
        if intake.deadline:
            self.assertEqual((copy.deadline - intake.deadline).days % 7, 0)  # on the same weekday


class FakeMoodle:
    """Moodle's web services as far as ConCampus calls them, kept in memory; users for these e-mail addresses only."""

    def __init__(self, emails):
        self.users = {e.lower(): n for n, e in enumerate(emails, 100)}
        self.categories, self.courses, self.enrolled = [], {}, set()
        self.groups, self.members = {}, defaultdict(set)

    def __call__(self, function, **p):
        if function == 'core_course_get_categories':
            return [c for c in self.categories if c['idnumber'] == p['criteria'][0]['value']]
        if function == 'core_course_create_categories':
            made = [{'id': len(self.categories) + n + 1, **c} for n, c in enumerate(p['categories'])]
            self.categories += made
            return made
        if function == 'core_course_create_courses':
            for c in p['courses']:
                self.courses[len(self.courses) + 1] = dict(c)
            return [{'id': len(self.courses)}]
        if function == 'core_course_get_courses_by_field':
            return {'courses': [{'id': i, **c} for i, c in self.courses.items() if c['idnumber'] == p['value']]}
        if function == 'core_course_update_courses':
            for c in p['courses']:
                self.courses[c['id']].update(c)
            return {'warnings': []}
        if function == 'core_user_get_users_by_field':
            return [{'id': self.users[v], 'email': v} for v in p['values'] if v in self.users]
        if function == 'enrol_manual_enrol_users':
            self.enrolled |= {(e['courseid'], e['userid'], e['roleid']) for e in p['enrolments']}
            return None
        if function == 'core_group_get_course_groups':
            return [{'id': n, **g} for n, g in self.groups.items() if g['courseid'] == p['courseid']]
        if function == 'core_group_create_groups':
            for g in p['groups']:
                self.groups[len(self.groups) + 1] = dict(g)
            return [{'id': len(self.groups)}]
        if function == 'core_group_get_group_members':
            return [{'groupid': g, 'userids': sorted(self.members[g])} for g in p['groupids']]
        if function in ('core_group_add_group_members', 'core_group_delete_group_members'):
            for m in p['members']:
                (self.members[m['groupid']].add if function.endswith('add_group_members') else self.members[m['groupid']].discard)(m['userid'])
            return None
        if function == 'enrol_manual_unenrol_users':
            out = {(e['courseid'], e['userid']) for e in p['enrolments']}
            self.enrolled = {e for e in self.enrolled if e[:2] not in out}
            return None
        raise AssertionError(function)


class MoodleTest(Seeded):
    def test_courses_and_their_people_follow_campus(self):
        """A semester's courses go to Moodle under semester, Fachgebiet and Lehrform, with their teachers and participants;
        whoever has no Moodle account yet waits, whoever leaves is taken out, a course called off is hidden."""
        course = Course.objects.filter(kind='course', term=Term.current(), cancelled=None, area__isnull=False, format__isnull=False, staff__isnull=False).annotate(n=Count('participations')).filter(n__gte=3).first()
        students = [p.study.person for p in course.participations.select_related('study__person')]
        teacher = course.staff.first()
        moodle = FakeMoodle([teacher.email, *(p.email for p in students[1:])])  # the first student has not signed in to Moodle yet
        self.assertGreaterEqual(sync(moodle)['ohne Moodle-Konto'], 1)
        mine = MoodleCourse.objects.get(course=course).moodle_id
        here = moodle.courses[mine]
        self.assertEqual((here['idnumber'], here['visible']), (f'campus-course-{course.pk}', True))
        names = {c['id']: c for c in moodle.categories}
        path = [names[here['categoryid']]]
        while path[-1]['parent']:
            path.append(names[path[-1]['parent']])
        self.assertEqual([c['name'] for c in reversed(path)], [course.term.name.replace('WiSe', 'Wintersemester').replace('SoSe', 'Sommersemester'), course.area.name, course.format.name])
        self.assertIn((mine, moodle.users[teacher.email.lower()], 3), moodle.enrolled)
        self.assertIn((mine, moodle.users[students[1].email.lower()], 5), moodle.enrolled)
        self.assertIn(str(teacher), here['summary'])  # the Kursinfo
        made = len(moodle.courses)
        self.assertEqual(sync(moodle, changed=True)['Kurse'], 0)  # each minute: nothing changed, nothing sent
        Participation.objects.filter(course=course, study__person=students[1]).delete()
        self.assertEqual(sync(moodle, changed=True)['Kurse'], 1)
        self.assertEqual(len(moodle.courses), made)  # each course once
        self.assertNotIn((mine, moodle.users[students[1].email.lower()], 5), moodle.enrolled)
        Course.objects.filter(pk=course.pk).update(cancelled=timezone.now())
        sync(moodle)
        self.assertFalse(moodle.courses[mine]['visible'])
        Course.objects.filter(pk=course.pk).update(cancelled=None)
        MoodleCourse.objects.all().delete()  # ConCampus's links lost, as from an older backup: Moodle's courses are taken up again
        sync(moodle)
        self.assertEqual((len(moodle.courses), MoodleCourse.objects.get(course=course).moodle_id), (made, mine))
        self.assertEqual(flat({'courses': [{'fullname': 'X', 'visible': True}]}), {'courses[0][fullname]': 'X', 'courses[0][visible]': 1})
