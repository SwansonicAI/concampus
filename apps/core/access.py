from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db import transaction

CAPABILITIES = [  # what an office may do; every Hochschule names its own offices and gives them these
    ('Studierende', [
        ('view_students', 'Studierende auflisten, suchen und exportieren'),
        ('view_files', 'Studienakten mit Studienverlauf und Leistungen einsehen, ohne persönliche Daten'),
        ('manage_students', 'Immatrikulieren, exmatrikulieren, Fachwechsel, Sperren und Stammdaten mit allen persönlichen Daten; Bescheide und Bescheinigungen für andere'),
        ('manage_reregistration', 'Rückmeldung verbuchen, bei fehlender Rückmeldung exmatrikulieren'),
        ('message_students', 'Nachrichten an ausgewählte Studierende senden'),
        ('dispose_files', 'Akten Ehemaliger nach Ablauf der Aufbewahrung dem Archiv anbieten'),
    ]),
    ('Bewerbungen', [
        ('view_applications', 'Alle Bewerbungen einsehen'),
        ('decide_applications', 'Über Bewerbungen entscheiden'),
        ('run_auditions', 'Eignungsprüfungen anlegen und durchführen, Kommissionen zusammenstellen, Unterlagen nach Frist annehmen'),
    ]),
    ('Prüfungen', [
        ('manage_exams', 'Alle Prüfungen sehen, anlegen, bearbeiten und Termine planen'),
        ('decide_results', 'Ergebnisse aus den Protokollen eintragen und freigeben; Zeugnisse, mit Geburtsdaten, Anschrift und Unterlagen der Studierenden'),
        ('appoint_examiners', 'Prüfende für ein Studienjahr bestellen'),
        ('correct_scores', 'Wertungen berichtigen, abweichende Noten mit Begründung festsetzen, freigegebene Ergebnisse wieder öffnen und zurücksetzen'),
        ('recognise', 'Leistungen anerkennen'),
        ('grant_compensation', 'Nachteilsausgleich gewähren, mit dem Attest dazu (Gesundheitsdaten)'),
    ]),
    ('Lehre', [
        ('manage_courses', 'Lehrveranstaltungen anlegen und bearbeiten: Titel, Lehrende, Termine, Plätze, Ensembles'),
        ('plan_teaching', 'Umfang (SWS), Lehrform und Module von Lehrveranstaltungen festlegen'),
        ('view_teaching_load', 'Deputat aller Lehrenden einsehen'),
        ('manage_teaching_load', 'Ermäßigungen der Lehrverpflichtung eintragen'),
        ('roll_over', 'Semesterwechsel: Lehrveranstaltungen ins nächste Semester übernehmen und planen'),
        ('publish_term', 'Ein geplantes Semester veröffentlichen'),
    ]),
    ('Studiengänge', [
        ('view_curriculum', 'Studiengänge mit Zahlen der Studierenden und Bewerbungen, als Kachel'),
        ('edit_curriculum', 'Studiengänge, Module und Hauptfächer bearbeiten'),
    ]),
    ('Räume', [
        ('book_any_room', 'Alle Räume und Kategorien ohne Buchungsregeln buchen'),
        ('view_bookings', 'Alle Buchungen mit Details sehen'),
        ('manage_bookings', 'Buchungen anderer ändern und stornieren'),
        ('manage_rooms', 'Räume, Raumfunktionen und Buchungskategorien pflegen'),
        ('manage_instruments', 'Leihinstrumente pflegen und verleihen'),
    ]),
    ('Internationales', [
        ('manage_mobility', 'Partnerhochschulen und Abkommen pflegen, Auslandsaufenthalte erfassen, Learning Agreements genehmigen'),
    ]),
    ('Verwaltung', [
        ('manage_terms', 'Semester anlegen und pflegen'),
        ('manage_institution', 'Hochschuldaten, Institute und Texte pflegen'),
        ('manage_accounts', 'Ämter, Berechtigungen und Konten verwalten'),
        ('view_statistics', 'Auswertungen aller Bereiche einsehen (Zahlen; Namen nur mit der Berechtigung des Bereichs)'),
        ('report_statistics', 'Amtliche Statistik der Studierenden und Prüfungen prüfen und liefern'),
    ]),
    ('Archiv', [
        ('assess_files', 'Angebotene Akten bewerten und archivwürdige übernehmen'),
    ]),
]
CODENAMES = [codename for _, group in CAPABILITIES for codename, _ in group]
EVERYWHERE = {'manage_reregistration', 'manage_terms', 'manage_institution', 'manage_accounts', 'view_statistics', 'report_statistics', 'dispose_files', 'assess_files', 'manage_mobility'}  # never limited to Institute
SHORT = {  # each right in a few words under its area, to take in at a glance; CAPABILITIES says it in full
    'view_students': 'Auflisten und exportieren', 'view_files': 'Studienakten einsehen', 'manage_students': 'Immatrikulieren und Stammdaten',
    'manage_reregistration': 'Rückmeldung verbuchen', 'message_students': 'Nachrichten senden', 'dispose_files': 'Akten dem Archiv anbieten',
    'view_applications': 'Bewerbungen einsehen', 'decide_applications': 'Über Bewerbungen entscheiden', 'run_auditions': 'Eignungsprüfungen durchführen',
    'manage_exams': 'Prüfungen anlegen und planen', 'decide_results': 'Ergebnisse freigeben, Zeugnisse', 'appoint_examiners': 'Prüfende bestellen',
    'correct_scores': 'Wertungen berichtigen', 'recognise': 'Leistungen anerkennen', 'grant_compensation': 'Nachteilsausgleich gewähren',
    'manage_courses': 'Lehrveranstaltungen anlegen', 'plan_teaching': 'SWS, Lehrform und Module', 'view_teaching_load': 'Deputat einsehen',
    'manage_teaching_load': 'Ermäßigungen eintragen', 'roll_over': 'Semesterwechsel', 'publish_term': 'Semester veröffentlichen',
    'view_curriculum': 'Studiengänge mit Zahlen', 'edit_curriculum': 'Studiengänge bearbeiten',
    'book_any_room': 'Alle Räume buchen', 'view_bookings': 'Alle Buchungen sehen', 'manage_bookings': 'Buchungen anderer ändern',
    'manage_rooms': 'Räume pflegen', 'manage_instruments': 'Leihinstrumente',
    'manage_mobility': 'Partner und Auslandsaufenthalte',
    'manage_terms': 'Semester pflegen', 'manage_institution': 'Hochschuldaten und Texte', 'manage_accounts': 'Ämter, Berechtigungen und Konten',
    'view_statistics': 'Auswertungen', 'report_statistics': 'Amtliche Statistik',
    'assess_files': 'Akten bewerten',
}
AREAS = {  # each area's icon: an app's, by its label, or as drawn
    'Studierende': 'students', 'Bewerbungen': 'admissions', 'Prüfungen': 'exams', 'Lehre': 'courses', 'Studiengänge': 'curriculum', 'Räume': 'rooms',
    'Internationales': 'mobility', 'Verwaltung': 'setup', 'Archiv': 'M3 4h18v4H3zM5 8v12h14V8M10 12h4',
}


def areas(marks=None, every=True):
    """CAPABILITIES to draw: [(area, icon, [(short, label, mark)])]. `marks` ({codename: 'done', or 'part' where limited to
    some Institute or Studiengänge; for a table, a list of them}) leaves out the areas it holds nothing in, and without
    `every` the rights it holds nothing for."""
    from django.apps import apps

    return [
        (area, getattr(apps.app_configs.get(AREAS[area]), 'icon', AREAS[area]), [(SHORT[c], label, (marks or {}).get(c, '')) for c, label in group if every or c in marks])
        for area, group in CAPABILITIES if marks is None or marks.keys() & {c for c, _ in group}
    ]


def kept(form, person, capability):
    """Save a form unless the record would end up beyond what the person may do with `capability` (Person.may): then
    nothing is saved, the form says why, and None comes back."""
    new = form.instance.pk is None
    with transaction.atomic():
        record = form.save()
        if person.may(capability, record):
            return record
        transaction.set_rollback(True)
    if new:
        form.instance.pk = None
    form.add_error(None, 'Das läge außerhalb der Institute und Studiengänge, für die Ihr Amt zuständig ist.')
    return None


ROLES = {'student': 'Studierende', 'applicant': 'Bewerbende', 'teacher': 'Lehrende', 'juror': 'Kommissionen von Eignungsprüfungen', 'alumni': 'Ehemalige'}


class Refused(PermissionDenied):
    """A page for some roles or rights only (`roles`, as require() takes them): the 403 page shows which, and the offices
    that hold each right."""

    def __init__(self, roles):
        super().__init__(f'Nur für {", ".join(roles)}')
        self.roles = roles


def require(*roles):
    """View decorator: the user needs one of these roles (capabilities, 'student', 'applicant', 'teacher'); else they
    see whom the page is for."""

    def decorator(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            if not request.user.roles & set(roles):
                raise Refused(roles)
            return view(request, *args, **kwargs)

        return guarded

    return decorator
