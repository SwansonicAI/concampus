from datetime import timedelta

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.db.models import Q
from django.dispatch import receiver
from django.utils import timezone

from .models import Person, SignIn

TRIES, HOLD = 5, timedelta(minutes=15)  # failures for a name from one address, and how long it is then held back there


def known(name):
    """Whom a sign-in name stands for: user name or e-mail address, in any letter case."""
    return Person.objects.filter(Q(username__iexact=name) | Q(email__iexact=name)).first() if name else None


def address(request):
    """The client's address as the proxy saw it."""
    return request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[-1].strip()[:45] if request else ''


def held(name, request):
    """Whether `name` has failed TRIES times from this address within HOLD, since it last signed in there."""
    person = known(name)
    tries = SignIn.objects.filter(ip=address(request), at__gte=timezone.now() - HOLD).filter(Q(person=person) if person else Q(username__iexact=name))
    if last := tries.filter(success=True).values_list('at', flat=True).first():
        tries = tries.filter(at__gt=last)
    return tries.filter(success=False).count() >= TRIES


@receiver(user_logged_in)
def signed_in(sender, request, user, **kwargs):
    SignIn.objects.create(username=user.username, person=user, ip=address(request), success=True)


@receiver(user_login_failed)
def failed(sender, credentials, request=None, **kwargs):
    name = credentials.get('username') or ''
    SignIn.objects.create(username=name[:150], person=known(name), ip=address(request), success=False)


class Login(ModelBackend):
    """Sign in with user name or e-mail address, in any letter case."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        person = known(username)
        if person is None:
            Person().set_password(password)  # same work as a real check, so timing reveals nothing
            return None
        if person.check_password(password) and self.user_can_authenticate(person):
            return person
