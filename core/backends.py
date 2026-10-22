from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from .models import Person


class Login(ModelBackend):
    """Sign in with user name or e-mail address, in any letter case."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        person = Person.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).first() if username else None
        if person is None:
            Person().set_password(password)  # same work as a real check, so timing reveals nothing
            return None
        if person.check_password(password) and self.user_can_authenticate(person):
            return person
