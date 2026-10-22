from django.core.exceptions import ValidationError
from django.test import TestCase

from .history import acting, restore
from .models import Change, Person


class HistoryTest(TestCase):
    def test_changes_are_kept_as_differences_and_undone_only_while_current(self):
        clerk = Person.objects.create(username='clerk')
        Person.objects.create(username='mei', last_name='Chen', address='Am Horn 1')
        mei = Person.objects.get(username='mei')
        mei.address, mei.password = 'Jakobstraße 7', 'secret'
        with acting(clerk):
            mei.save()
            mei.save()  # nothing changed: nothing kept

        change = Change.objects.get(action='changed')
        self.assertEqual((change.by, change.person), (clerk, mei))
        self.assertEqual(change.fields, {'address': ['Am Horn 1', 'Jakobstraße 7'], 'password': [None, None]})

        restore(change)
        mei.refresh_from_db()
        self.assertEqual((mei.address, mei.password), ('Am Horn 1', 'secret'))
        self.assertEqual(Change.objects.first().action, 'restored')
        with self.assertRaises(ValidationError):  # already undone: its 'after' is no longer current
            restore(change)
