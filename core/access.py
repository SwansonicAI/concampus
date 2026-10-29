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
    ]),
    ('Aufnahme', [
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
        ('manage_courses', 'Veranstaltungen anlegen und bearbeiten: Titel, Lehrende, Termine, Plätze, Ensembles'),
        ('plan_teaching', 'Umfang (SWS), Lehrform und Module von Veranstaltungen festlegen'),
        ('view_teaching_load', 'Deputat aller Lehrenden einsehen'),
        ('manage_teaching_load', 'Ermäßigungen der Lehrverpflichtung eintragen'),
        ('roll_over', 'Semesterwechsel'),
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
    ('Verwaltung', [
        ('manage_terms', 'Semester anlegen und pflegen'),
        ('manage_institution', 'Hochschuldaten, Institute und Texte pflegen'),
        ('manage_accounts', 'Ämter, Rechte und Konten verwalten'),
        ('view_statistics', 'Auswertungen aller Bereiche einsehen (Zahlen; Namen nur mit dem Recht des Bereichs)'),
        ('report_statistics', 'Amtliche Statistik der Studierenden und Prüfungen prüfen und liefern'),
    ]),
]
CODENAMES = [codename for _, group in CAPABILITIES for codename, _ in group]
EVERYWHERE = {'manage_reregistration', 'manage_terms', 'manage_institution', 'manage_accounts', 'view_statistics', 'report_statistics'}  # never limited to Institute


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


def needs(roles):
    """Whom a page is for, in a sentence: the roles, and the rights with the offices that hold them."""
    from .models import Office

    labels = dict(c for _, group in CAPABILITIES for c in group)
    lines = [ROLES[r] for r in roles if r in ROLES] + [f'Recht „{labels[r]}“: {Office.holding(r, remit=True) or "noch kein Amt"}' for r in roles if r in labels]
    return 'Diese Seite ist für:\n' + '\n'.join(f'– {line}' for line in lines)


def require(*roles):
    """View decorator: the user needs one of these roles (capabilities, 'student', 'applicant', 'teacher'); else they
    hear whom the page is for."""

    def decorator(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            if not request.user.roles & set(roles):
                raise PermissionDenied(needs(roles))
            return view(request, *args, **kwargs)

        return guarded

    return decorator
