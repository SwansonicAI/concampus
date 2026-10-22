from django.http import HttpResponse

from .history import acting


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
