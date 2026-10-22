from functools import wraps

from django.core.exceptions import PermissionDenied

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
        ('view_curriculum', 'Studiengänge einsehen'),
        ('edit_curriculum', 'Studiengänge, Module und Hauptfächer bearbeiten'),
    ]),
    ('Räume', [
        ('book_any_room', 'Alle Räume und Kategorien ohne Buchungsregeln buchen'),
        ('view_bookings', 'Alle Buchungen mit Details sehen'),
        ('manage_bookings', 'Buchungen anderer ändern und stornieren'),
        ('manage_rooms', 'Räume, Raumfunktionen und Buchungskategorien pflegen'),
    ]),
    ('Verwaltung', [
        ('manage_terms', 'Semester anlegen und pflegen'),
        ('manage_institution', 'Hochschuldaten, Institute und Texte pflegen'),
        ('manage_accounts', 'Ämter, Rechte und Konten verwalten'),
    ]),
]
CODENAMES = [codename for _, group in CAPABILITIES for codename, _ in group]


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
