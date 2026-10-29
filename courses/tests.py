from datetime import timedelta

from django.test import Client
from django.utils import timezone

from core.models import Holiday, Person, Term
from rooms.models import Room
from core.testing import Seeded, flashes
from students.models import Study
from .models import Course, Ensemble, Participation, Signup
from .views import deputat, loads


class RolloverTest(Seeded):
    def test_next_term_gets_the_courses_and_lesson_classes(self):
        before = Course.objects.filter(term__name='SoSe 2027', kind__in=['course', 'lesson']).count()
        dekanat = self.login('dekanatsreferat')
        kept, *taken = dekanat.get('/lehre/semesterwechsel/').context['pending']  # one left out, e.g. for a sabbatical
        r = dekanat.post('/lehre/semesterwechsel/', {'course': [c.pk for c in taken]}, follow=True)
        self.assertEqual(flashes(r), [f'{len(taken)} Veranstaltungen nach SoSe 2027 übernommen.'])
        self.assertEqual(Course.objects.filter(term__name='SoSe 2027', kind__in=['course', 'lesson']).count(), before + len(taken))
        self.assertFalse(Course.objects.filter(term__name='SoSe 2027', title=kept.title).exists())
        self.assertTrue(Participation.objects.filter(course__term__name='SoSe 2027', course__kind='lesson').exists())
        self.assertEqual(self.login('student').post('/lehre/semesterwechsel/').status_code, 403)


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
            'study': student.pk, 'name': 'Duo Violine/Violoncello', 'members': cellist.person.email, 'repertoire': 'Ravel: Sonate',
        }, follow=True)
        self.assertEqual(flashes(r), ['„Duo Violine/Violoncello“ angemeldet. Wer es betreut, erfahren Sie im Postfach.'])
        duo = Ensemble.objects.get(name='Duo Violine/Violoncello')
        self.assertEqual(set(duo.members.all()), {student, cellist})
        self.assertTrue(chamber.participations.filter(study=cellist).exists())
        wendt = Person.objects.get(username='lehre')
        before = deputat(wendt.teaching.filter(term=chamber.term), wendt)
        self.login('lehre').post(f'/lehre/ensemble/{duo.pk}/', {'coach': wendt.pk})
        self.assertEqual(deputat(wendt.teaching.filter(term=chamber.term), wendt), before + chamber.hours)

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
