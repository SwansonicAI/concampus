from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from django.conf import settings

from .apps import tiles
from .models import Institution
from .views import admitted, demo_people


def platform(request):
    """Layout, main navigation, Postfach count and the Hochschule's own data. Pages fetched for the side panel get a
    bare layout."""
    context = {'institution': SimpleLazyObject(Institution.get), 'today': timezone.localdate()}  # {% now %} would not follow a demo's day
    if request.headers.get('X-Panel'):
        return context | {'base': 'core/panel.html'}
    context['base'] = 'core/base.html'
    context['sso'] = bool(settings.SAML['idp'])
    if settings.DEMO and not request.user.is_authenticated and admitted(request):
        context['demo'] = demo_people  # called only where a page lists them
    if request.user.is_authenticated:
        menu = [(app, title, app.pages(request.user)) for app, title in tiles(request.user)]
        context.update(menu=menu, unread=request.user.messages.filter(read=None).count())
        if settings.DEMO:
            context.update(day_control=True, demo_day=request.session.get('demo_day', ''))  # a day of its own, or today
    return context
