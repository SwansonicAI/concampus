from django.test import Client

from core.testing import Seeded, flashes
from curriculum.models import Module
from students.models import Study
from .models import Component, Mobility, Partner


class MobilityTest(Seeded):
    def test_a_learning_agreement_from_nomination_to_recognition(self):
        """The office notes a stay at a partner; the student fills in the Learning Agreement and signs, the office approves
        and notes the partner's approval; a line changed afterwards asks for every signature anew; back home, the
        Anerkennung opens filled in with what the Learning Agreement says."""
        office = self.login('international')
        study = Study.objects.filter(status='enrolled', program__degree='B.Mus.', mobilities=None).select_related('person').first()
        partner = Partner.objects.get(name__startswith='Royal Irish')
        r = office.post('/international/', {'who': study.person.matriculation, 'partner': partner.pk, 'kind': 'studium', 'start': '01.02.2027', 'end': '30.06.2027'}, follow=True)
        mobility = Mobility.objects.get(study=study)
        self.assertEqual(mobility.agreement.partner, partner)
        student = Client()
        student.force_login(study.person)
        self.assertTrue(student.get(f'/international/{mobility.pk}/').context['editing'])
        module = Module.objects.filter(program=study.program, children=None, credits__gt=0).exclude(participations__study=study, participations__passed=True).first()
        student.post(f'/international/{mobility.pk}/', {'zeile': '1', 'table': 'A', 'title': 'Performance Studies', 'code': 'PS1', 'credits': '15'})
        student.post(f'/international/{mobility.pk}/', {'zeile': '1', 'table': 'B', 'module': module.pk, 'credits': str(module.credits)})
        student.post(f'/international/{mobility.pk}/', {'unterschreiben': '1'})
        self.assertIn('2 Learning Agreements genehmigen', [t['text'] for t in office.get('/').context['tasks']])  # with the one waiting already
        office.post(f'/international/{mobility.pk}/', {'genehmigen': '1'})
        office.post(f'/international/{mobility.pk}/', {'partner': '1', 'am': '20.11.2026'})
        mobility.refresh_from_db()
        self.assertTrue(mobility.signed)
        student.post(f'/international/{mobility.pk}/', {'zeile': '1', 'table': 'A', 'title': 'Chamber Music', 'credits': '5'})
        mobility.refresh_from_db()
        self.assertEqual((mobility.changes, mobility.signed_student), (1, None))  # a change: everyone signs anew
        stranger = Client()
        stranger.force_login(Study.objects.filter(status='enrolled').exclude(person=study.person).first().person)
        self.assertEqual(stranger.get(f'/international/{mobility.pk}/').status_code, 403)
        office.post(f'/international/{mobility.pk}/', {'stand': 'zurueck'})
        board = self.login('pruefungsausschuss')
        page = board.get(f'/international/{mobility.pk}/')
        link = next(c for c in page.context['home'])[0]
        form = board.get(f'/pruefungen/anerkennung/?studium={study.pk}&module={link.module_id}&origin=9990{partner.country}&place={partner.name}&credits=5').context['form']
        self.assertEqual((form.initial['module'], form.initial['place']), (str(link.module_id), partner.name))
        self.assertEqual(Component.objects.filter(mobility=mobility).count(), 3)
        self.assertTrue(flashes(r))
