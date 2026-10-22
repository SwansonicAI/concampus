from core.testing import Seeded, flashes
from .models import Course, Participation


class RolloverTest(Seeded):
    def test_next_term_gets_the_courses_and_lesson_classes(self):
        before = Course.objects.filter(term__name='SoSe 2027', kind__in=['course', 'lesson']).count()
        r = self.login('dekanat').post('/lehre/semesterwechsel/', follow=True)
        self.assertEqual(flashes(r), ['Veranstaltungen nach SoSe 2027 übernommen.'])
        self.assertGreater(Course.objects.filter(term__name='SoSe 2027', kind__in=['course', 'lesson']).count(), before)
        self.assertTrue(Participation.objects.filter(course__term__name='SoSe 2027', course__kind='lesson').exists())
        self.assertEqual(self.login('student').post('/lehre/semesterwechsel/').status_code, 403)
