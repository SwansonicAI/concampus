from .apps import tiles


def platform(request):
    """Layout, main navigation and Postfach count. Pages fetched for the side panel get a bare layout."""
    if request.headers.get('X-Panel'):
        return {'base': 'core/panel.html'}
    context = {'base': 'core/base.html'}
    if request.user.is_authenticated:
        context.update(nav=tiles(request.user), unread=request.user.messages.filter(read=None).count())
    return context
