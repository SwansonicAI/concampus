from datetime import timedelta

from django.db.models import Count
from django.test import Client
from django.utils import timezone

from core.models import Holiday, Person, Term
from rooms.models import Room
from core.testing import Seeded, flashes
from students.models import Study
from .models import Course, Ensemble, MoodleCourse, Participation, Signup
from .moodle import flat, sync
from .views import deputat, loads


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


class FakeMoodle:
    """Moodle's web services as far as Campus calls them, kept in memory; users for these e-mail addresses only."""

    def __init__(self, emails):
        self.users = {e.lower(): n for n, e in enumerate(emails, 100)}
        self.categories, self.courses, self.enrolled = [], {}, set()

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
        made = len(moodle.courses)
        Participation.objects.filter(course=course, study__person=students[1]).delete()
        sync(moodle)
        self.assertEqual(len(moodle.courses), made)  # each course once
        self.assertNotIn((mine, moodle.users[students[1].email.lower()], 5), moodle.enrolled)
        Course.objects.filter(pk=course.pk).update(cancelled=timezone.now())
        sync(moodle)
        self.assertFalse(moodle.courses[mine]['visible'])
        Course.objects.filter(pk=course.pk).update(cancelled=None)
        MoodleCourse.objects.all().delete()  # Campus's links lost, as from an older backup: Moodle's courses are taken up again
        sync(moodle)
        self.assertEqual((len(moodle.courses), MoodleCourse.objects.get(course=course).moodle_id), (made, mine))
        self.assertEqual(flat({'courses': [{'fullname': 'X', 'visible': True}]}), {'courses[0][fullname]': 'X', 'courses[0][visible]': 1})
