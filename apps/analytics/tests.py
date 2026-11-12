import csv
import io
import re
from dataclasses import replace
from html import unescape

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core import destatis
from core.models import Term
from core.testing import Seeded
from students.models import Study
from .figures import MEASURES, OTHERS, table, window


class FigureTest(Seeded):
    """Every split adds up: a dimension never loses or doubles rows, folding included."""

    def test_splits_add_up_to_the_total(self):
        now = Term.current()
        for key in ['bewerbungen', 'pruefungen', 'belegungen', 'veranstaltungen']:
            m = MEASURES[key]
            for dim in m.dimensions:
                with self.subTest(measure=key, dimension=dim):
                    terms = window(m, 10) if m.dimensions[dim].time else [now]
                    t = table(m, [dim], terms)
                    self.assertEqual(sum(t.row_totals.values()), t.total)
            t = table(m, [m.default, 'semester'], window(m, 5))
            self.assertEqual(sum(t.cells.values()), t.total)

    def test_one_person_counts_once(self):
        m = MEASURES['studierende']
        m = replace(m, dimensions={**m.dimensions, 'staat': replace(m.dimensions['staat'], top=3)})  # three nationalities, the rest folded
        t = table(m, ['staat', 'geschlecht'], [Term.current()])
        self.assertIn(OTHERS, t.rows)
        self.assertEqual(sum(t.row_totals.values()), t.total)
        self.assertEqual(sum(t.cells.values()), t.total)


class AccessTest(Seeded):
    def test_names_behind_a_number_only_with_the_right_of_its_area(self):
        self.assertEqual(self.login('student').get('/auswertungen/').status_code, 403)
        praesidium = self.login('praesidium')
        self.assertEqual(praesidium.get('/auswertungen/').status_code, 200)
        self.assertEqual(praesidium.get('/auswertungen/datensaetze/?was=studierende&f_hauptfach=1').status_code, 403)
        names = self.login('asa').get('/auswertungen/datensaetze/?was=studierende&f_hauptfach=1')
        self.assertContains(names, '/studierende/')

    def test_every_link_on_the_overview_opens(self):
        asa = self.login('asa')
        with CaptureQueriesContext(connection) as queries:
            page = asa.get('/auswertungen/').content.decode()
        self.assertLess(len(queries), 150)  # a bounded number per chart, none per row
        links = {unescape(href) for href in re.findall(r'href="(/auswertungen/[^"]+)"', page)}
        self.assertGreater(len(links), 30)
        for link in links:
            with self.subTest(link=link):
                self.assertEqual(asa.get(link).status_code, 200)
        self.assertEqual(asa.get('/auswertungen/abfrage/?was=bewerbungen&nach=hauptfach&und=ergebnis&format=csv')['Content-Type'], 'text/csv; charset=utf-8')
        lines = asa.get('/auswertungen/abfrage/?was=bewerbungen&nach=studienjahr&und=ergebnis&darstellung=linien').context['chart']['data']
        self.assertEqual({s['label'] for s in lines['series']} >= {'abgelehnt', 'immatrikuliert'}, True)
        self.assertEqual({len(s['values']) for s in lines['series']}, {len(lines['x'])})


class OfficialTest(Seeded):
    def test_records_fit_the_layout_and_gaps_are_named(self):
        asa, summer, winter = self.login('asa'), Term.objects.get(name='SoSe 2026'), Term.objects.get(name='WiSe 2026/27')
        for term, key, layout in [
            (winter, 'studierende', destatis.RECORD), (summer, 'pruefungen', destatis.RECORD), (winter, 'gasthoerer', destatis.GUESTS),
            (winter, 'promovierende', destatis.DOCTORAL),
        ]:
            with self.subTest(file=key):
                rows = list(csv.reader(io.StringIO(asa.get(f'/auswertungen/amtlich/?semester={term.pk}&datei={key}').content.decode()), delimiter=';'))
                self.assertEqual(rows[0], [name for _, name, _ in layout])
                self.assertTrue(rows[1:])
                for row in rows[1:]:  # every value as long as its field, or empty
                    self.assertEqual([len(value) in (0, length) for value, (_, _, length) in zip(row, layout, strict=True)], [True] * len(row))
        term = summer
        person = Study.objects.filter(status='enrolled', terms__term=term).first().person
        person.home = ''
        person.save()
        self.assertContains(asa.get(f'/auswertungen/amtlich/?semester={term.pk}'), 'Heimatwohnsitz')
