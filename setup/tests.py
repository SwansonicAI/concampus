import re

from django.core import mail
from django.test import Client

from core.models import Change, Grant, Membership, Office, Person, Term
from core.testing import Seeded, flashes
from curriculum.models import Module, Program, Subject
from rooms.models import Room
from .editors import EDITORS

PANEL = {'X-Panel': '1'}


class EditorTest(Seeded):
    """Each list staff keep up: only with its right, every change in the history, deletions restorable."""

    def samples(self):
        return {  # a new record per editor, and one field to change afterwards
            'aemter': ({'name': 'Studierendensekretariat', 'capabilities': ['view_students']}, {'name': 'Sekretariat'}),
            'konten': ({'first_name': 'Ina', 'last_name': 'Neu', 'email': 'ina.neu@example.org', 'username': 'ina.neu', 'is_active': 'on'}, {'last_name': 'Neumann'}),
            'semester': ({
                'name': 'WiSe 2027/28', 'start': '01.10.2027', 'end': '31.03.2028', 'lectures_start': '11.10.2027', 'lectures_end': '05.02.2028',
                'reregistration_start': '01.06.2027', 'deadline': '15.07.2027', 'fee': '250',
            }, {'fee': '260'}),
            'institute': ({'name': 'Instrumentenbau'}, {'name': 'Instrumentenbau und Akustik'}),
            'studiengaenge': ({'degree': 'M.Mus.', 'name': 'Alte Musik', 'version': '2027', 'semesters': '4', 'attempts': '3', 'rounding': 'half_up', 'mean_rounding': 'cut', 'kind': '7'}, {'semesters': '5'}),
            'hauptfaecher': ({'name': 'Laute'}, {'name': 'Laute und Theorbe'}),
            'eignungspruefungen': ({'title': 'Videostufe Laute', 'term': Term.upcoming().pk, 'video': 'on', 'deadline': '01.12.2026', 'pass_mark': '16.1', 'max_points': '25'}, {'capacity': '2'}),
            'raeume': ({'name': 'FH 999', 'building': 'Fürstenhaus', 'function': 'uebung', 'capacity': '2'}, {'capacity': '3'}),
            'unterlagen': ({'name': 'Repertoireliste', 'condition': 'always', 'required': 'on', 'order': '9'}, {'note': 'Werke mit Satzangaben.'}),
            'raumfunktionen': ({'name': 'Tonstudio', 'order': '9'}, {'central': 'on'}),
            'kategorien': ({'name': 'Generalprobe', 'tone': 'teach', 'teachers': 'on', 'participants': '0', 'blocks': 'on', 'order': '9'}, {'public': 'on'}),
        }

    def test_each_list_needs_its_right(self):
        student = self.login('student')
        self.assertEqual(student.get('/einstellungen/').status_code, 403)
        for key, e in EDITORS.items():
            with self.subTest(key=key):
                self.assertEqual(student.get(f'/einstellungen/{key}/neu/').status_code, 403)
        self.assertEqual(self.login('dekanat').get('/einstellungen/raeume/').status_code, 403)
        self.assertEqual(self.login('veranstaltungsbuero').get('/einstellungen/raeume/').status_code, 200)

    def test_create_change_delete_restore(self):
        admin = self.login('admin')
        for key, (data, change) in self.samples().items():
            e = EDITORS[key]
            with self.subTest(key=key):
                self.assertEqual(admin.get(f'/einstellungen/{key}/').status_code, 200)
                r = admin.post(f'/einstellungen/{key}/neu/', data, headers=PANEL)
                self.assertEqual(r.status_code, 204, r.context and r.context['form'].errors)
                obj = e.model.objects.order_by('-pk').first()
                table = e.model._meta.label_lower
                self.assertTrue(Change.objects.filter(table=table, row=obj.pk, action='created').exists())
                admin.post(f'/einstellungen/{key}/{obj.pk}/', {**data, **change})
                self.assertTrue(Change.objects.filter(table=table, row=obj.pk, action='changed').exists())
                if e.deletable:
                    admin.post(f'/einstellungen/{key}/{obj.pk}/loeschen/')
                    self.assertFalse(e.model.objects.filter(pk=obj.pk).exists())
                    gone = Change.objects.get(table=table, row=obj.pk, action='deleted')
                    admin.post(f'/verlauf/{gone.pk}/zuruecksetzen/')
                    self.assertTrue(e.model.objects.filter(pk=obj.pk).exists())

    def test_records_in_use_stay(self):
        violin = Subject.objects.get(name='Violine')
        r = self.login('admin').post(f'/einstellungen/hauptfaecher/{violin.pk}/loeschen/', follow=True)
        self.assertIn('wird noch verwendet', flashes(r)[0])
        self.assertTrue(Subject.objects.filter(pk=violin.pk).exists())

    def test_people_are_not_deleted_here(self):
        self.assertEqual(self.login('admin').post(f'/einstellungen/konten/{Person.objects.get(username="lehre").pk}/loeschen/').status_code, 404)

    def test_an_office_is_what_its_rights_say(self):
        campus = self.login('campus')
        clerk = Person.objects.create(username='clerk')
        office = Office.objects.create(name='Bibliothek')
        campus.post(f'/einstellungen/aemter/{office.pk}/', {'name': 'Bibliothek', 'capabilities': ['view_students']})
        campus.post(f'/einstellungen/konten/{clerk.pk}/', {'username': 'clerk', 'groups': [office.pk], 'is_active': 'on'})
        self.assertFalse(Person.objects.get(pk=clerk.pk).roles)  # nothing yet: a second person confirms rights and members
        self.assertEqual(campus.post('/einstellungen/freigaben/', {'art': 'mitglied', 'id': Membership.objects.get(person=clerk).pk, 'decision': 'yes'}).status_code, 404)
        second = self.login('jan.reuter')
        second.post('/einstellungen/freigaben/', {'art': 'recht', 'id': Grant.objects.get(office=office).pk, 'decision': 'yes'})
        second.post('/einstellungen/freigaben/', {'art': 'mitglied', 'id': Membership.objects.get(person=clerk).pk, 'decision': 'yes'})
        self.assertIn('view_students', Person.objects.get(pk=clerk.pk).roles)
        joined = Change.objects.filter(person=clerk, table='core.person').first()
        self.assertEqual(joined.fields['groups'], [[], [office.pk]])


class CurriculumEditorTest(Seeded):
    def test_modules_are_added_under_a_node_and_never_under_themselves(self):
        dekanat = self.login('dekanat')
        program = Program.objects.get(degree='B.Mus.', name='Streichinstrumente und Harfe')
        root = program.modules.filter(parent=None).first()
        dekanat.post(f'/einstellungen/module/neu/?studiengang={program.pk}&unter={root.pk}', {'parent': root.pk, 'code': 'X-01', 'title': 'Neues Modul', 'credits': '5', 'weight': '0'})
        module = Module.objects.get(code='X-01')
        self.assertEqual((module.program, module.parent), (program, root))
        r = dekanat.post(f'/einstellungen/module/{root.pk}/', {'parent': module.pk, 'title': root.title, 'credits': root.credits, 'weight': '0'})
        self.assertIn('parent', r.context['form'].errors)
        self.assertIn('Modul hinzufügen', dekanat.get(f'/studiengaenge/{program.pk}/').text)


class InvitationTest(Seeded):
    def test_a_new_account_sets_its_password_from_the_invitation(self):
        campus = self.login('campus')
        campus.post('/einstellungen/konten/neu/', {'first_name': 'Ida', 'last_name': 'Neu', 'email': 'ida.neu@example.org', 'username': 'ida.neu', 'is_active': 'on'})
        person = Person.objects.get(username='ida.neu')
        with self.captureOnCommitCallbacks(execute=True):
            campus.post(f'/einstellungen/konten/{person.pk}/einladung/')
        link = re.search(r'https?://\S+', mail.outbox[-1].body).group()
        anon = Client()
        form = anon.get(link, follow=True).redirect_chain[-1][0]
        anon.post(form, {'new_password1': 'Geige-Weimar-2026', 'new_password2': 'Geige-Weimar-2026'})
        self.assertTrue(Client().login(username='ida.neu', password='Geige-Weimar-2026'))
