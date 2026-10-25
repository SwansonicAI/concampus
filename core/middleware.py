from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse
from django.urls import reverse

from .history import acting
from .views import admitted


def gate(get_response):
    """In the demo, strangers give the shared access code first (core.views.gate); whoever is signed in is past it."""

    def middleware(request):
        if settings.DEMO_CODE and not request.user.is_authenticated and not admitted(request) and request.path != reverse('gate'):
            return redirect_to_login(request.get_full_path(), reverse('gate'))
        return get_response(request)

    return middleware


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
