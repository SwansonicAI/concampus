"""What the tests share: the demo data, the day it tells of, and acting as one of its people."""
import re
import shutil
import tempfile
from contextlib import ExitStack
from io import StringIO
from unittest import mock

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.runner import DiscoverRunner, get_max_test_processes
from django.utils import timezone

from .management.commands.seed import TODAY
from .models import Person

MEDIA = tempfile.mkdtemp(prefix='concampus-test-')


def on_the_day():
    """The clock on the demo data's day, at the time of day it is: its deadlines don't pass under the tests."""
    real = timezone.now
    shift = timezone.localtime(real()).date() - TODAY

    def now():
        return real() - shift

    stack = ExitStack()
    stack.enter_context(mock.patch.object(timezone, 'now', now))
    for field in (f for model in apps.get_models() for f in model._meta.fields if f.default is real):  # default=timezone.now
        stack.enter_context(mock.patch.object(field, 'default', now))
    return stack


class Runner(DiscoverRunner):
    """Runs the test classes in parallel processes unless `--parallel 1`; seeds the test database once, before each process
    gets its copy. Each test's changes are rolled back after it."""

    def __init__(self, parallel=0, **kwargs):
        super().__init__(parallel=parallel or get_max_test_processes(), **kwargs)

    def setup_databases(self, aliases=None, **kwargs):
        parallel, self.parallel = self.parallel, 0
        databases = super().setup_databases(aliases=aliases, **kwargs)
        self.parallel = parallel
        if not aliases:  # tests without a database: none was made, so none to seed
            return databases
        with on_the_day():
            call_command('seed', stdout=StringIO())
        if parallel > 1:
            for n in range(1, parallel + 1):
                connection.creation.clone_test_db(suffix=str(n), verbosity=self.verbosity, keepdb=self.keepdb)
        return databases


@override_settings(DEMO_CODE='')  # no demo gate; the test of it sets a code
class Case(TestCase):
    """A test on the demo data's day."""

    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(on_the_day())
        super().setUpClass()


class Empty(Case):
    """Tests on a database without the demo data; it is back for the next class."""

    @classmethod
    def setUpTestData(cls):
        call_command('flush', interactive=False, verbosity=0)


@override_settings(MEDIA_ROOT=MEDIA)
class Seeded(Case):
    """Tests against the demo data."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def login(self, username):
        client = Client()
        person = Person.objects.get(username=username)
        if not person.is_active:  # the demo's system account, which nobody signs in with, stands in for all rights
            Person.objects.filter(pk=person.pk).update(is_active=True)
        client.force_login(person)
        return client


def flashes(response):
    """The flash messages a page shows."""
    return re.findall(r'<p class="flash[^"]*">(.*?)</p>', response.content.decode())
