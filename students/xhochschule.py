"""Bescheinigungen as XHochschule 1.2 data, besides the printed pages: the Immatrikulationsbescheinigung and its version
of § 9 BAföG, the Exmatrikulationsbescheinigung, and the Studienzeitbescheinigung as XHochschule's Rentenbescheinigung.
Each is checked against the standard's schemas (students/schemas/) before it goes out. Not yet: the Leistungsübersicht,
Zeugnis and Diploma Supplement, and an eIDAS seal (ds:Signature)."""
from datetime import date
from functools import cache
from pathlib import Path

from django.utils import timezone
from lxml import etree
from lxml.builder import ElementMaker

from core.models import Edition, Institution, Term
from .models import StudyTerm

XHS, XBD = 'http://xhochschule.de/def/xhochschule/1.2/xsd', 'http://xbildung.de/def/xbildung/1.2/xsd'
X, B, E = ElementMaker(namespace=XHS, nsmap={'xhs': XHS, 'xbd': XBD}), ElementMaker(namespace=XBD), ElementMaker()
LANG = '{http://www.w3.org/XML/1998/namespace}lang'
STATUS = 'http://xhochschule.de/def/xhochschule/1.0/code/'
FULL_TIME = 'http://xbildung.de/def/xbildung/2025-02-04/code/lernzeitmodell/vollzeit'  # Campus knows no part-time study
EQF = {'bachelor': '6', 'master': '7', 'diplom': '7'}  # Program.Level
SCHEMAS = Path(__file__).parent / 'schemas' / 'xhochschule-1.2'


class Local(etree.Resolver):
    """The schemas' imports from the copies here, not the web."""

    def resolve(self, url, pubid, context):
        return self.resolve_filename(str(SCHEMAS / url.rsplit('/', 1)[-1]), context)


@cache
def schema():
    parser = etree.XMLParser()
    parser.resolvers.add(Local())
    return etree.XMLSchema(etree.parse(str(SCHEMAS / 'xhochschule.xsd'), parser))


def text(tag, value):
    return X(tag, str(value), {LANG: 'de-DE'})


def code(tag, value, maker=X, **listed):
    return maker(tag, E.code(value), **listed)


def day(value):
    return value.isoformat()


def begun(first):
    """The day of the first enrolment: a term's first day; for one elsewhere, as Person.first_term names it, the first of
    April or October."""
    if isinstance(first, Term):
        return first.start
    winter, year = first.startswith('WiSe'), int(first.split()[1][:4])
    return date(year, 10 if winter else 4, 1)


def edition():
    """The official key lists' edition in use, as the version of a code list from them."""
    found = Edition.objects.filter(listing='hochschule').order_by('-effective').first()
    return day(found.effective) if found else '1'


def hochschule(tag='ausstellendeHochschule'):
    institution = Institution.get()
    signature = [code('hochschulsignatur', institution.university, listVersionID=edition())] if institution.university else []
    return X(tag, *signature, text('name', institution.name))


def person(study, first, ended=None, bafoeg=False):
    """The student: name, birth, and unless for BAföG, Matrikelnummer and semesters here."""
    p = study.person
    names = [X.familienname(X.name(p.last_name)), X.vorname(X.name(p.first_name) if p.first_name else X.nichtVorhanden('true'))]
    birth = X.geburt(
        *([X.datum(B.jahrMonatTag(day(p.birth_date)))] if p.birth_date else []), *([X.geburtsort(B.ort(p.birth_place))] if p.birth_place else []),
    )  # both needed for BAföG: the schema check says so where one is missing
    if bafoeg:
        return X.studierender(X.name(*names), birth, X.ersteinschreibung(day(begun(first))))
    leave = StudyTerm.objects.filter(study__person=p, leave=True, withdrawn=False, term__start__lte=timezone.localdate()).values('term').distinct().count()
    valid = [X.beginn(day(begun(first))), *([X.ende(day(ended))] if ended else [])]
    return X.studierender(
        X.nameNatuerlichePerson(*names), birth,
        X.matrikelnummer(X.id(p.matriculation), X.gueltigkeit(*valid), X.beschreibung(f'Matrikelnummer der {Institution.get().name}')),
        X.ersteinschreibung(day(begun(first))), X.hochschulsemester(str(study.university_semester)), X.urlaubssemester(str(leave)),
    )


def subject(study, semester=None):
    program, eqf = study.program, EQF.get(study.program.level)
    return X.studienfach(
        text('name', f'{program.name}, Hauptfach {study.subject}'), text('angestrebterAbschluss', program.qualification or program.degree),
        *([code('schluesselEQF', eqf)] if eqf else []), *([X.fachsemester(str(semester))] if semester is not None else []),
    )


def period(term):
    return X.studienzeitraum(X.zeitraum(X.beginn(day(term.start)), X.ende(day(term.end))), text('semesterbezeichnung', term.name))


def full_time():
    return X.lernzeitmodell(code('code', FULL_TIME))


def on_leave(leave):
    return X.beurlaubungsstatus(code('code', f'{STATUS}beurlaubungsstatus/{"beurlaubt" if leave else "nicht_beurlaubt"}'))


def document(kind, title, *body):
    today = day(timezone.localdate())
    root = X(kind, text('titel', title), X.ausstellungsdatum(today), X.gueltigkeitsdatum(today), code('sprache', 'DEU'), *body)
    schema().assertValid(root)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', pretty_print=True)


def enrolment(study, term, registration, fs, first, check, bafoeg=False):
    """Immatrikulationsbescheinigung of `term`, or with `bafoeg` its version of § 9 BAföG; `check`: its Prüfcode and the
    address to check it at."""
    kind, title = ('immatrikulationsbescheinigungBAfoeGP9', 'Immatrikulationsbescheinigung nach § 9 BAföG') if bafoeg else ('immatrikulationsbescheinigung', 'Immatrikulationsbescheinigung')
    return document(
        kind, title, hochschule(), person(study, first, bafoeg=bafoeg), subject(study, fs), full_time(), period(term),
        X.immatrikulationsstatus(code('code', f'{STATUS}immatrikulationsstatus/immatrikuliert')), on_leave(registration and registration.leave),
        X.verifikation(X.schluessel(check[0]), X.adresse(check[1]), X.inhaltstyp('text/html')),
    )


def exmatriculation(study, first):
    """Exmatrikulationsbescheinigung: the last day enrolled, and the study as one span."""
    return document(
        'exmatrikulationsbescheinigung', 'Exmatrikulationsbescheinigung', X.exmatrikulationsdatum(day(study.since)),
        person(study, first, ended=study.since), hochschule(),
        X.studienabschnitt(X.zusammenfassung(
            X.zeitraum(X.beginn(day(study.start_term.start)), X.ende(day(study.since))), subject(study, study.semester), full_time(),
            X.regelstudienzeit(str(study.program.semesters)),
        )),
    )


def periods(study, rows, first):
    """Studienzeitbescheinigung as XHochschule's Rentenbescheinigung: each semester registered, with its study, Fachsemester
    and leave with its ground, as the standard asks of a leave (pension insurance counts some grounds), and the day a
    study ended within it."""
    studies = {s for _, s, _, _ in rows}
    ended = None if any(s.status == s.Status.ENROLLED for s in studies) else max((s.since for s in studies), default=None)
    return document(
        'rentenbescheinigung', 'Studienzeitbescheinigung', person(study, first, ended), hochschule(),
        *(X.studienabschnitt(
            period(term), subject(s, semester), full_time(), X.regelstudienzeit(str(s.program.semesters)), on_leave(r.leave),
            *([code('beurlaubungsgrund', r.leave_reason, listVersionID=edition())] if r.leave and r.leave_reason else []), hochschule('ausbildungsstaette'),
            *([X.vorzeitigBeendet(day(s.since))] if s.status in s.FORMER and term.start <= s.since < term.end else []),
        ) for term, s, r, semester in rows),
    )
