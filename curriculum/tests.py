from django.test import TestCase

from .models import Module, Program


class CurriculumTest(TestCase):
    def test_choice_branch_is_complete_at_its_own_credits(self):
        program = Program.objects.create(name='Test', degree='B.Mus.', version=2026, semesters=8)
        catalogue = Module.objects.create(program=program, code='F', title='Wahlkatalog', credits=10)
        a, b, c = (Module.objects.create(program=program, parent=catalogue, code=f'F{n}', title=f'W{n}', credits=5) for n in range(3))

        [branch] = program.curriculum({a.pk: None})
        self.assertEqual((branch.earned, branch.offered), (5, 15))

        [branch] = program.curriculum({a.pk: None, b.pk: None, c.pk: None})
        self.assertEqual(branch.earned, 10)
