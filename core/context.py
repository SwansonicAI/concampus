from django.utils.functional import SimpleLazyObject

from .apps import tiles
from .models import Institution


def platform(request):
    """Layout, main navigation, Postfach count and the Hochschule's own data. Pages fetched for the side panel get a
    bare layout."""
    context = {'institution': SimpleLazyObject(Institution.get)}
    if request.headers.get('X-Panel'):
        return context | {'base': 'core/panel.html'}
    context['base'] = 'core/base.html'
    if request.user.is_authenticated:
        context.update(nav=tiles(request.user), unread=request.user.messages.filter(read=None).count())
    return context
