"""What the tests share: the demo data, and acting as one of its people."""
import re
import shutil
import tempfile
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.test.runner import DiscoverRunner

from .models import Person

MEDIA = tempfile.mkdtemp(prefix='hochadmin-test-')


class Runner(DiscoverRunner):
    """Seeds the test database once; each test's changes are rolled back after it."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.DEMO_CODE = ''  # no demo gate; the test of it sets a code

    def setup_databases(self, **kwargs):
        databases = super().setup_databases(**kwargs)
        call_command('seed', stdout=StringIO())
        return databases


class Empty(TestCase):
    """Tests on a database without the demo data; it is back for the next class."""

    @classmethod
    def setUpTestData(cls):
        call_command('flush', interactive=False, verbosity=0)


@override_settings(MEDIA_ROOT=MEDIA)
class Seeded(TestCase):
    """Tests against the demo data."""

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
