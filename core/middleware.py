import time

from django.conf import settings
from django.db import transaction
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse
from django.urls import reverse

from .history import acting
from .models import looked_up
from .views import admitted


def gate(get_response):
    """In the demo, strangers give the shared access code first (core.views.gate); whoever is signed in is past it,
    calendar apps fetch the feeds without it, whoever was handed a Bescheinigung checks it without it, and the Hochschule's
    login answers and reads ConCampus's metadata without it."""

    def middleware(request):
        free = request.path in (reverse('gate'), reverse('students:verify'), reverse('sso_acs'), reverse('sso_metadata')) or request.path.endswith('.ics')
        if settings.DEMO_CODE and not request.user.is_authenticated and not admitted(request) and not free:
            return redirect_to_login(request.get_full_path(), reverse('gate'))
        return get_response(request)

    return middleware


def renewal(get_response):
    """Signed out after two hours without a request (SESSION_COOKIE_AGE): a request renews the session's time once five
    minutes have passed since it was last renewed, not every request, which would write to the database each time."""

    def middleware(request):
        response = get_response(request)
        session = getattr(request, 'session', None)
        if session is not None and not session.is_empty() and time.time() - session.get('renewed', 0) > 300:
            session['renewed'] = int(time.time())
        return response

    return middleware


def lookups(get_response):
    """A GET request looks up what does not change while it is answered only once (core.models.once)."""

    def middleware(request):
        token = looked_up.set({} if request.method in ('GET', 'HEAD') else None)
        try:
            return get_response(request)
        finally:
            looked_up.reset(token)

    return middleware


class Atomic:
    """A change and its history are saved together or not at all: the view of a request that may change something runs
    in one transaction, as ATOMIC_REQUESTS would. A GET changes a single row at most and goes without, two round trips
    to the database fewer."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view, args, kwargs):
        if request.method not in ('GET', 'HEAD'):
            with transaction.atomic():
                return view(request, *args, **kwargs)


def history(get_response):
    """What a request changes, its user changed."""

    def middleware(request):
        with acting(request.user):
            return get_response(request)

    return middleware


def panel(get_response):
    """A form sent by script (from the side panel, or an upload) that succeeds answers 204 with where its redirect
    leads, so the script can choose: show that page in the panel, or load it. The message waits for either."""

    def middleware(request):
        response = get_response(request)
        if request.method == 'POST' and request.headers.get('X-Panel') and response.status_code == 302:
            return HttpResponse(status=204, headers={'X-Location': response['Location']})
        return response

    return middleware
