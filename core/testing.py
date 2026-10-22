"""What the tests share: the demo data, and acting as one of its people."""
import re
import shutil
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from .models import Person

MEDIA = tempfile.mkdtemp(prefix='hochadmin-test-')


@override_settings(MEDIA_ROOT=MEDIA)
class Seeded(TestCase):
    """Tests against the demo data, fresh for every test class."""

    @classmethod
    def setUpTestData(cls):
        call_command('seed', stdout=StringIO())

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def login(self, username):
        client = Client()
        client.force_login(Person.objects.get(username=username))
        return client


def flashes(response):
    """The flash messages a page shows."""
    return re.findall(r'<p class="flash[^"]*">(.*?)</p>', response.content.decode())
