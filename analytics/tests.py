import re
from dataclasses import replace
from html import unescape

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Term
from core.testing import Seeded
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
