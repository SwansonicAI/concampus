from django.apps import AppConfig
from django.urls import reverse

from core.apps import Tile, feature, page, plural


class DocumentsConfig(Tile, AppConfig):
    name = 'documents'
    verbose_name = 'Dokumente'
    order = 9
    icon = 'M14 3H6v18h12V7zM14 3v4h4M9 12h6M9 16h6'
    url = 'documents:index'

    def title(self, roles):
        if roles & {'word_texts', 'design_papers'}:
            return 'Dokumente'

    def figure(self, person):
        from core.models import Text

        return f'{plural(Text.objects.count(), "Text", "Texte")} in eigenem Wortlaut'

    def pages(self, person):
        from .catalogue import KINDS
        from .views import kinds

        return [page(KINDS[kind][0], f'documents:{kind}') for kind in kinds(person)]

    def features(self, person):
        from .catalogue import KINDS
        from .views import kinds

        words = {
            'bescheide': 'dokumente texte wortlaut vorlagen briefe rechtsbehelfsbelehrung zulassung ablehnung',
            'bescheinigungen': 'dokumente immatrikulationsbescheinigung zeugnis urkunde diploma supplement leihvertrag notenliste unterschriften absender',
            'nachrichten': 'dokumente texte wortlaut e-mail postfach',
            'seiten': 'dokumente texte anmeldeseite barrierefreiheit einwilligung datenschutz',
        }
        return [feature(KINDS[kind][0], reverse(f'documents:{kind}'), words[kind]) for kind in kinds(person)] + [
            feature('Briefvorlage', reverse('documents:paper', args=['brief']), 'dokumente brief briefkopf briefbogen logo schrift gestaltung layout vorlage'),
        ] * ('design_papers' in person.roles)
