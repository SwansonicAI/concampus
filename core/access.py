from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db import transaction

CAPABILITIES = [  # what an office may do; every Hochschule names its own offices and gives them these
    ('Studierende', [
        ('view_students', 'Studierende auflisten, suchen und exportieren'),
        ('view_files', 'Alle Studienakten und Leistungsübersichten einsehen'),
        ('manage_students', 'Immatrikulieren, exmatrikulieren, Fachwechsel, Sperren und Stammdaten; Bescheide und Bescheinigungen für andere'),
        ('manage_reregistration', 'Rückmeldung verbuchen, bei fehlender Rückmeldung exmatrikulieren'),
        ('message_students', 'Nachrichten an ausgewählte Studierende senden'),
    ]),
    ('Aufnahme', [
        ('view_applications', 'Alle Bewerbungen einsehen'),
        ('decide_applications', 'Über Bewerbungen entscheiden'),
        ('run_auditions', 'Eignungsprüfungen anlegen und durchführen, Wertungen festsetzen, Unterlagen nach Frist annehmen'),
    ]),
    ('Prüfungen', [
        ('manage_exams', 'Alle Prüfungen sehen, anlegen, bearbeiten und Termine planen'),
        ('decide_results', 'Ergebnisse festsetzen, freigeben und wieder öffnen; Zeugnisse'),
        ('recognise', 'Leistungen anerkennen'),
    ]),
    ('Lehre', [
        ('manage_courses', 'Veranstaltungen anlegen und alle bearbeiten'),
        ('view_teaching_load', 'Deputat aller Lehrenden einsehen'),
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
    ]),
]
CODENAMES = [codename for _, group in CAPABILITIES for codename, _ in group]
EVERYWHERE = {'manage_reregistration', 'roll_over', 'manage_terms', 'manage_institution', 'manage_accounts', 'view_statistics'}  # never limited to Institute


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


def require(*roles):
    """View decorator: the user needs one of these roles (capabilities, 'student', 'applicant', 'teacher')."""

    def decorator(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            if not request.user.roles & set(roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return guarded

    return decorator
