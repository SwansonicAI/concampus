from django.apps import AppConfig, apps


class CoreConfig(AppConfig):
    name = 'core'
    verbose_name = 'Plattform'

    def ready(self):
        from . import history, text  # noqa: F401  text registers the fold() SQL function
        history.connect()


class Tile:
    """Mixin for app configs that appear as a tile on the start page and in the main navigation."""

    order = 0
    icon = ''  # SVG path data, 24×24, stroked
    url = ''  # URL name of the app's start page

    def title(self, roles):
        """Tile title for someone with these roles, or None if the app has nothing for them."""
        raise NotImplementedError

    def figure(self, person):
        """One live figure shown on the tile."""
        return ''

    def tasks(self, person):
        """What the person should do in this app now: [{'text', 'url', 'due'}]."""
        return []

    def features(self, person):
        """Places in the app the search offers to this person: [{'title', 'url', 'words'}]."""
        return []


def plural(n, one, many):
    return f'{n} {one if n == 1 else many}'


def feature(title, url, words=''):
    """A place in an app; the search finds it by its title or by these other words."""
    return {'title': title, 'url': url, 'words': words}


def task(text, url, due=None):
    return {'text': text, 'url': url, 'due': due}


def tile_apps():
    return sorted((app for app in apps.get_app_configs() if isinstance(app, Tile)), key=lambda app: app.order)


def tiles(person):
    """(app, title) for every app the person can use, in tile order."""
    return [(app, title) for app in tile_apps() if (title := app.title(person.roles))]
