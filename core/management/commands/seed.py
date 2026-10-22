"""Demo data on the structure of HfM FRANZ LISZT Weimar.

Study plans come from core/management/plans.py (the SVPP of the B.Mus. and the FPSO M.Mus.). Semester dates, fees
and deadlines follow the Studienjahresrahmenplan 2026/27; course titles and times follow the published
Lehrveranstaltungsverzeichnisse WiSe 2026/27; buildings and the 25-point Eignungsprüfung follow the Hochschule's
rules. Every person is invented.
"""
import random
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone

from admissions.forms import INSURERS
from admissions.views import ACCEPT_DAYS, DECISIONS
from core import history
from core.models import OFFICES, Change, Institute, Message, Person, Term
from courses.models import Course, Participation, Score
from courses.panel import mean
from curriculum.models import Module, Program, Subject
from rooms.models import Booking, Room
from students.models import APPEAL, Document, Study, StudyTerm
from ..plans import HF, JENA, PIANO, PROGRAMS, RESEARCH, SPEECH, STRINGS, WINDS, WS

FEE = Decimal('341.30')  # Semesterbeitrag WiSe 2026/27: Studierendenwerk 95,00 + Deutschlandsemesterticket 226,80 + Studierendenrat 19,50
FEES = {  # published Semesterbeiträge from WiSe 2023/24; earlier ones approximated, SoSe 2027 as WiSe 2026/27
    'SoSe 2022': Decimal('205.30'), 'WiSe 2022/23': Decimal('211.30'), 'SoSe 2023': Decimal('219.80'), 'WiSe 2023/24': Decimal('230.30'),
    'SoSe 2024': Decimal('223.80'), 'WiSe 2024/25': Decimal('279.40'), 'SoSe 2025': Decimal('279.40'), 'WiSe 2025/26': Decimal('313.30'),
    'SoSe 2026': Decimal('323.30'),
}
FEMALE = {
    'Anna', 'Clara', 'Lea', 'Nora', 'Hannah', 'Ida', 'Greta', 'Svea', 'Maja', 'Frieda', 'Marlene', 'Mei Lin', 'Xinyi', 'Jiayi', 'Seo-yeon', 'Ha-eun',
    'Aiko', 'Haruka', 'Yuki', 'Zofia', 'Marta', 'Aleksandra', 'Chiara', 'Valentina', 'Giulia', 'Sofiia', 'Anastasiia', 'Kateryna',
}
MIDDLE = {'w': ['Sophie', 'Marie', 'Charlotte', 'Luise', 'Elisabeth'], 'm': ['Maximilian', 'Alexander', 'Johannes', 'Friedrich', 'Wilhelm']}
DEMO_VIDEO = (  # stand-in for every audition video: a real performance under CC BY 3.0, via Wikimedia Commons
    'Platzhalter: Schumann, Fantasiestücke op. 73 (N. Hakhnazaryan, R. Rabinovich; CC BY 3.0)',
    'https://upload.wikimedia.org/wikipedia/commons/transcoded/a/a9/'
    'Robert_Schumann_-_Fantasiest%C3%BCcke%2C_Op._73_%28Narek_Hakhnazaryan%2C_Cello%2C_Roman_Rabinovich%2C_Piano%2C_2008%29.webm/'
    'Robert_Schumann_-_Fantasiest%C3%BCcke%2C_Op._73_%28Narek_Hakhnazaryan%2C_Cello%2C_Roman_Rabinovich%2C_Piano%2C_2008%29.webm.480p.vp9.webm',
)
VIDEO_SUBJECTS = {  # where the HfM's Eignungsprüfung starts with a video (Alles über die Eignungsprüfung)
    'Violine', 'Viola', 'Violoncello', 'Flöte', 'Oboe', 'Klarinette', 'Posaune', 'Schlagwerk', 'Gesang', 'Klavier', 'Jazz-Gesang', 'Orgel',
}
COUNTRIES = {'chinesisch': 'China', 'südkoreanisch': 'Südkorea', 'japanisch': 'Japan', 'polnisch': 'Polen', 'italienisch': 'Italien', 'ukrainisch': 'Ukraine'}
FREE_DAYS = {date(2026, 10, 3), date(2026, 10, 31), *(date(2026, 12, 21) + timedelta(days=n) for n in range(14))}
EXAM_DAYS = [d for d in (date(2027, 2, 8) + timedelta(days=n) for n in range(19)) if d.weekday() < 5]  # Prüfungszeitraum 08.–26.02.2027
LECTURES = {  # published lecture periods; other terms are approximated
    'WiSe 2025/26': (date(2025, 10, 8), date(2026, 2, 10)), 'SoSe 2026': (date(2026, 4, 8), date(2026, 7, 14)),
    'WiSe 2026/27': (date(2026, 10, 5), date(2027, 2, 5)), 'SoSe 2027': (date(2027, 4, 7), date(2027, 7, 13)),
}
ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII']
INSTITUTES = {  # the HfM's ten Institute
    'AM': 'Alte Musik', 'BS': 'Blasinstrumente und Schlagwerk', 'DO': 'Dirigieren und Opernkorrepetition', 'GM': 'Gesang und Musiktheater',
    'GI': 'Gitarre', 'KL': 'Klavier', 'MK': 'Musikpädagogik und Kirchenmusik', 'MW': 'Musikwissenschaft, Kulturmanagement, Musiktheorie',
    'NJ': 'Neue Musik und Jazz', 'SH': 'Streichinstrumente und Harfe',
}
SUBJECT_INSTITUTE = {
    **dict.fromkeys(STRINGS, 'SH'), **dict.fromkeys(WINDS, 'BS'), 'Gesang': 'GM', 'Jazz-Gesang': 'NJ', 'Klavier': 'KL', 'Orgel': 'MK',
    'Musikpädagogik': 'MK', 'Musikwissenschaft': 'MW', 'Kulturmanagement': 'MW',
}
TEACHER_INSTITUTE = {  # teachers without a Hauptfach of their own
    'brandt': 'MW', 'brandauer': 'MW', 'roth': 'MW', 'voss': 'MW', 'scholz': 'MW', 'hausmann': 'MW', 'schulte': 'KL', 'engel': 'KL',
    'neumann': 'MK', 'hagen': 'MK', 'wiegand': 'MK', 'ferreira': 'DO', 'popescu': 'DO', 'hein': 'DO', 'martens': 'DO',
    'falk': 'GM', 'kessler': 'GM', 'bellini': 'GM', 'weber': 'NJ', 'brueck': 'AM',
}

# Rooms and their purposes as the HfM's Asimut lists them; rooms the Asimut groups leave out get the Institut their purpose names.
ROOMS = {
    'Fürstenhaus': 'Festsaal:Festsaal; FH 022:Sitzungsraum; FH 115:Senatssaal; FH 024:Unterrichtsraum Liedgestaltung; FH 105:Übungsraum Alte Musik; '
                   'FH 106:Unterrichtsraum Alte Musik; FH 107:Unterrichtsraum Alte Musik; FH 108:Salon; FH 109:Unterrichtsraum Kammermusik; '
                   'FH 110:Übungsraum Kammermusik; FH 204:Unterrichtsraum Violoncello; FH 206:Unterrichtsraum Violoncello; FH 207:Unterrichtsraum Kontrabass; '
                   'FH 208:Unterrichtsraum Kontrabass; FH 212:Übungsraum Kammermusik; FH 213:Unterrichtsraum Klavier; FH 214:Unterrichtsraum Klavier; '
                   'FH 215:Unterrichtsraum Klavier; FH 216:Unterrichtsraum Klavier; FH 217:Unterrichtsraum Liedgestaltung; FH 218:Unterrichtsraum Klavier; '
                   'FH 304:Unterrichtsraum Kontrabass; FH 307:Unterrichtsraum Werkstudium; FH 308:Unterrichtsraum Werkstudium; FH 310:Übungsraum Streicher/Harfe; '
                   'FH 311:Übungsraum Kammermusik; FH 312:Unterrichtsraum Violine; FH 313:Unterrichtsraum Viola; FH 314:Unterrichtsraum Blockflöte; '
                   'FH 315:Unterrichtsraum Klavier; FH 316:Unterrichtsraum Klavier; FH 317:Unterrichtsraum Streicher; FH 318:Unterrichtsraum Violine; '
                   'FH 319:Unterrichtsraum Werkstudium; FH 320:Unterrichtsraum Violine; FH 321:Unterrichtsraum Viola; FH 322:Unterrichtsraum Violine; '
                   'FH 323:Unterrichtsraum Violine; FH 324:Unterrichtsraum Kammermusik; FH 326:Studio für elektroakustische Musik; '
                   'FH 327:Studio für elektroakustische Musik',
    'Palais': 'Saal Am Palais:Saal; PA 014:Unterrichtsraum Orgel; PA 017:Werkstatt Klavierstimmer; PA 025:Unterrichtsraum Bandraum; '
              'PA 101:Unterrichtsraum Klavier; PA 104:Unterrichtsraum Chorleitung; PA 105:Unterrichtsraum Gesang; PA 108:Unterrichtsraum Gesang; '
              'PA 109:Überaum Orgel; PA 112:Saalnebenraum; PA 202:Übungsraum Alte Musik; PA 204:Übungsraum; PA 205:Unterrichtsraum Schulpraktisches Klavierspiel; '
              'PA 206:Unterrichtsraum Schulpraktisches Klavierspiel; PA 207:Unterrichtsraum Gesang; PA 209:Unterrichtsraum Orgel; PA 212:Tonstudio; '
              'PA 301:Unterrichtsraum Schulpraktisches Klavierspiel; PA 303:Übungsraum; PA 304:Unterrichtsraum Klavier; PA 305:Unterrichtsraum Klavier; '
              'PA 306:Unterrichtsraum Klavier; PA 307:Unterrichtsraum Schulpraktisches Klavierspiel; PA 308:Übungsraum Kirchenmusik; '
              'PA 310:Unterrichtsraum Sprecherziehung; PA 311:Seminarraum; PA 312:Ensembleraum; PA 313:Unterrichtsraum Gesang; PA 314:Unterrichtsraum Gesang',
    'Hochschulzentrum am Horn': 'HzH 313:Hörsaal; HzH 316:Seminarraum 1; HzH 320:Seminarraum 2; HzH 003:Übungsraum Bläser/Schlagwerk; '
                                'HzH 004:Unterrichtsraum Posaune; HzH 005:Unterrichtsraum Tuba; HzH 006:Unterrichtsraum Flöte; HzH 007:Unterrichtsraum Oboe; '
                                'HzH 008:Unterrichtsraum Klarinette; HzH 009:Unterrichtsraum Werkstudium; HzH 014:Unterrichtsraum Schlagwerk; '
                                'HzH 015:Unterrichtsraum Schlagwerk; HzH 016:Unterrichtsraum Werkstudium; HzH 017:Unterrichtsraum Fagott; '
                                'HzH 018:Unterrichtsraum Rhythmik; HzH 019:Übungsraum Rhythmik; HzH 103:Unterrichtsraum Dirigieren; HzH 104:Unterrichtsraum Dirigieren; '
                                'HzH 105:Unterrichtsraum Opernkorrepetition; HzH 106:Unterrichtsraum Klavier; HzH 107:Unterrichtsraum Klavier; '
                                'HzH 108:Unterrichtsraum Chordirigieren; HzH 112:Übungsraum Schlagwerk; HzH 113:Übungsraum Schlagwerk; HzH 114:Tonstudio; '
                                'HzH 115:Tonstudio; HzH 116:Unterrichtsraum Dirigieren; HzH 117:Unterrichtsraum Dirigieren; HzH 118:Übungsraum Dirigieren/Gesang; '
                                'HzH 119:Unterrichtsraum Jazz; HzH 120:Unterrichtsraum Werkstudium; HzH 121:Unterrichtsraum Jazz; HzH 122:Unterrichtsraum Jazz; '
                                'HzH 123:Unterrichtsraum Jazz; HzH 203:Unterrichtsraum Trompete; HzH 204:Unterrichtsraum Gehörbildung/Musiktheorie; '
                                'HzH 205:Unterrichtsraum Gehörbildung/Musiktheorie; HzH 206:Unterrichtsraum Gehörbildung/Musiktheorie; '
                                'HzH 207:Unterrichtsraum Gehörbildung/Musiktheorie; HzH 208:Unterrichtsraum Gehörbildung/Musiktheorie; HzH 213:Übungsraum Jazz; '
                                'HzH 214:Übungsraum Jazz; HzH 215:Unterrichtsraum Harfe; HzH 216:Übungsraum Bläser/Schlagwerk; HzH 217:Unterrichtsraum Horn; '
                                'HzH 218:Unterrichtsraum Jazz; HzH 219:Unterrichtsraum Jazz; HzH 220:Unterrichtsraum Jazz; HzH 221:Unterrichtsraum Jazz; '
                                'HzH 222:Unterrichtsraum Jazz; HzH 301:Ensembleraum Bläser; HzH 304:Ensembleraum Jazz; HzH 305:Projektraum; HzH 306:Projektraum; '
                                'HzH 309:Projektraum; HzH 311:Projektraum; ' + '; '.join(f'HzH K{n}:Übungsraum Bläser' for n in range(19, 27)) + '; '
                                'HzH K27:Hörlabor Vorraum; HzH K28:Hörlabor; HzH K29:Hörlabor',
    'Beethovenhaus': 'Jenny-Fleischer-Alt-Saal:Saal (BH 003); BH 002:Unterrichtsraum Gesang; BH 005:Unterrichtsraum Opernschule; BH 006:Unterrichtsraum Gesang; '
                     'BH 008:Unterrichtsraum Gesang; BH 009:Übungsraum Gesang; BH 011:Übungsraum Gesang; BH 012:Übungsraum Gesang; '
                     'BH 013:Unterrichtsraum Werkstudium; BH 102:Unterrichtsraum Gesang; BH 103:Übungsraum Gesang; BH 104:Übungsraum Gesang; '
                     'BH 105:Übungsraum Gesang; BH 108:Unterrichtsraum Werkstudium; BH 109:Unterrichtsraum Gesang; BH 110:Übungsraum Gesang; '
                     'BH 111:Übungsraum Gesang; BH 112:Unterrichtsraum Gesang; BH 113:Übungsraum Gesang; BH 114:Unterrichtsraum Gesang; BH 201:Übungsraum; '
                     'BH 202:Übungsraum Gesang; BH 203:Übungsraum Gesang; BH 205:Übungsraum Gesang; BH K11:Unterrichts- und Konferenzraum',
    'Haydnhaus': 'HA 001:Unterrichtsraum Akkordeon; HA 002:Unterrichtsraum Akkordeon; HA 004:Übungsraum Akkordeon/Klavier; HA 005:Übungsraum Akkordeon/Klavier; '
                 'HA 101:Übungsraum Akkordeon/Gitarre; HA 102:Übungsraum Akkordeon/Gitarre; HA 103:Übungsraum Akkordeon; HA 104:Übungsraum Akkordeon',
    'Bachhaus': '; '.join(f'BA {n}:Unterrichtsraum Gitarre' for n in ('001', '006', '007', '103', '104', '105', '106')),
    'Belvedere': 'Studiotheater:Theater mit Bühne und Orchestergraben',
    'Forum Seebach': 'Forum Seebach:Saal',
    'Weimarhalle': 'Weimarhalle großer Saal:Konzertsaal',
    'Stadtschloss': 'Stadtschloss neuer Veranstaltungssaal:Veranstaltungssaal',
    'Musikgymnasium Belvedere': 'Musikgymnasium Belvedere großer Saal:Konzertsaal',
    'Herz-Jesu-Kirche': 'Herz Jesu:Kirche mit Orgel',
}
ROOM_INSTITUTES = {  # the Institut groups in Asimut
    'AM': 'FH 105, FH 106, FH 107, FH 314, PA 202',
    'BS': 'HzH 003, HzH 004, HzH 005, HzH 006, HzH 007, HzH 008, HzH 009, HzH 014, HzH 015, HzH 016, HzH 017, HzH 112, HzH 113, HzH 120, HzH 203, '
          'HzH 216, HzH 217, HzH 301, ' + ', '.join(f'HzH K{n}' for n in range(19, 27)),
    'DO': 'HzH 103, HzH 104, HzH 105, HzH 106, HzH 107, HzH 108, HzH 116, HzH 117, HzH 118',
    'GM': 'Jenny-Fleischer-Alt-Saal, BH 002, BH 005, BH 006, BH 008, BH 009, BH 011, BH 012, BH 013, BH 102, BH 103, BH 104, BH 105, BH 108, BH 109, '
          'BH 110, BH 111, BH 112, BH 113, BH 114, BH 201, BH 202, BH 203, BH 205, BH K11, PA 310',
    'GI': 'BA 001, BA 006, BA 007, BA 103, BA 104, BA 105, BA 106, HA 101, HA 102',
    'KL': 'FH 024, FH 109, FH 110, FH 213, FH 214, FH 215, FH 216, FH 217, FH 218, FH 315, FH 316, FH 317, HA 001, HA 002, HA 004, HA 005, HA 101, HA 102, '
          'HA 103, HA 104, FH 212',
    'MK': 'Saal Am Palais, PA 014, PA 025, PA 101, PA 104, PA 105, PA 108, PA 109, PA 112, PA 202, PA 204, PA 205, PA 206, PA 207, PA 209, PA 301, PA 303, '
          'PA 304, PA 305, PA 306, PA 307, PA 308, PA 310, PA 311, PA 312, PA 313, PA 314, HzH 018, HzH 019',
    'MW': 'HzH 313, HzH 316, HzH 320, FH 022, FH 115, HzH 204, HzH 205, HzH 206, HzH 207, HzH 208, HzH K27, HzH K28, HzH K29',
    'NJ': 'FH 326, FH 327, HzH 119, HzH 121, HzH 122, HzH 123, HzH 213, HzH 214, HzH 218, HzH 219, HzH 220, HzH 221, HzH 222, HzH 304, HzH 114, HzH 115',
    'SH': 'FH 204, FH 206, FH 207, FH 208, FH 304, FH 307, FH 308, FH 310, FH 312, FH 313, FH 317, FH 318, FH 319, FH 320, FH 321, FH 322, FH 323, '
          'FH 311, FH 324, HzH 215',
}
EVENT_ROOMS = {'Festsaal', 'Jenny-Fleischer-Alt-Saal', 'Saal Am Palais', 'Studiotheater', 'FH 108', 'Forum Seebach', 'Weimarhalle großer Saal',
               'Stadtschloss neuer Veranstaltungssaal', 'Musikgymnasium Belvedere großer Saal'}
CHAMBER_ROOMS = {'FH 110', 'FH 212', 'FH 311', 'FH 324', 'PA 025', 'PA 312', 'HzH 304'}  # Funktion | Kammermusik
ORGAN_ROOMS = {'PA 014', 'PA 109', 'PA 112', 'PA 209', 'PA 308', 'Herz Jesu'}  # Funktion | Orgel
VENUES = {  # seats and instruments of the halls; Asimut lists neither, these are estimates
    'Festsaal': (200, 'Steinway D, Bösendorfer Imperial'), 'Saal Am Palais': (120, 'Jehmlich-Orgel, Steinway D'),
    'Jenny-Fleischer-Alt-Saal': (90, 'Steinway D'), 'Studiotheater': (140, 'Drehbühne, Orchestergraben'), 'FH 108': (40, 'Steinway B'),
    'Forum Seebach': (80, 'Flügel'), 'Weimarhalle großer Saal': (1000, 'Konzertflügel, Orchesterpodium'),
    'Stadtschloss neuer Veranstaltungssaal': (200, 'Flügel'), 'Musikgymnasium Belvedere großer Saal': (300, 'Steinway D'), 'Herz Jesu': (300, 'Orgel'),
    'HzH 313': (80, 'Flügel, Beamer'), 'FH 115': (30, 'Beamer'), 'FH 022': (14, 'Bildschirm'),
}


def furnish(name, purpose):
    """(function, seats, equipment) of a room, from what Asimut calls it."""
    if name in VENUES and name not in ('HzH 313', 'FH 115', 'FH 022'):
        return Room.Function.ORGAN if name == 'Herz Jesu' else Room.Function.EVENT, *VENUES[name]
    p = purpose.lower()
    if 'sitzung' in p or 'senat' in p:
        return Room.Function.MEETING, *VENUES[name]
    if 'hörsaal' in p:
        return Room.Function.SEMINAR, *VENUES[name]
    if 'seminar' in p or 'konferenz' in p:
        return Room.Function.SEMINAR, 24, 'Klavier, Beamer'
    if 'werkstatt' in p:
        return Room.Function.WORKSHOP, 2, 'Werkbank'
    if 'studio' in p or 'hörlabor' in p:
        return Room.Function.STUDIO, 8, 'Aufnahmetechnik' if 'studio' in p else 'Kopfhörerplätze'
    if name in ORGAN_ROOMS:
        return Room.Function.ORGAN, 8, 'Orgel'
    if name in CHAMBER_ROOMS or 'ensemble' in p or 'projekt' in p or 'band' in p:
        return Room.Function.CHAMBER, 16, 'Flügel, Notenpulte' if 'jazz' not in p and 'band' not in p else 'Flügel, Schlagzeug, Verstärker'
    equipment = ('Marimba, Pauken' if 'schlagwerk' in p else 'Flügel, Spiegel' if 'gesang' in p or 'lied' in p or 'oper' in p
                 else 'Zwei Flügel' if 'dirig' in p or 'klavier' in p else 'Cembalo' if 'alte musik' in p else 'Flügel, Verstärker' if 'jazz' in p else 'Klavier')
    if 'übungsraum' in p or 'überaum' in p:
        return Room.Function.PRACTICE, 3, equipment
    return Room.Function.TEACHING, 6, equipment


LESSON_ROOMS = {  # where each Hauptfach is taught, as the rooms are named
    'Violine': ['FH 312', 'FH 318', 'FH 320', 'FH 322', 'FH 323'], 'Viola': ['FH 313', 'FH 321'], 'Violoncello': ['FH 204', 'FH 206'],
    'Kontrabass': ['FH 207', 'FH 208', 'FH 304'], 'Harfe': ['HzH 215'], 'Flöte': ['HzH 006'], 'Oboe': ['HzH 007'], 'Klarinette': ['HzH 008'],
    'Fagott': ['HzH 017'], 'Horn': ['HzH 217'], 'Trompete': ['HzH 203'], 'Posaune': ['HzH 004'], 'Tuba': ['HzH 005'], 'Schlagwerk': ['HzH 014', 'HzH 015'],
    'Gesang': ['BH 002', 'BH 006', 'BH 008', 'BH 102', 'BH 109', 'BH 112', 'BH 114', 'PA 105', 'PA 108', 'PA 207', 'PA 313', 'PA 314'],
    'Jazz-Gesang': ['HzH 119', 'HzH 121'], 'Klavier': ['FH 213', 'FH 214', 'FH 215', 'FH 216', 'FH 218', 'FH 315', 'FH 316', 'PA 101', 'PA 304', 'PA 305', 'PA 306'],
    'Orgel': ['PA 014', 'PA 209'], PIANO: ['PA 205', 'PA 206', 'PA 301', 'PA 307'],
    WS: ['FH 307', 'FH 308', 'FH 319', 'HzH 009', 'HzH 016', 'HzH 120', 'BH 013', 'BH 108'], SPEECH: ['PA 310'],
}
SEMINAR_ROOMS = ['HzH 316', 'HzH 320', 'HzH 204', 'HzH 205', 'PA 311', 'HzH 018', 'HzH 313']
FIXED = {  # WiSe 2026/27 times from the Lehrveranstaltungsverzeichnisse: room, weekday, start, minutes
    'Musikgeschichte im Überblick II': ('HzH 313', 0, '11:15', 90),
    'Musikgeschichte im Überblick III': ('HzH 313', 1, '11:15', 90),
    '„Pour la fin du temps“ – Jüdisch-christliche Endzeitvorstellungen in der Musik': ('HzH 313', 2, '15:15', 90),
    'Allgemeine Musiklehre': ('HzH 313', 4, '09:15', 75),
    'Instrumentenkunde/Akustik': ('HzH 313', 2, '11:15', 90),
    'Höranalyse 1/Werkanalyse 1': ('HzH 207', 0, '15:15', 120),
    'Formenlehre': ('HzH 313', 0, '17:15', 90),
    'Werkanalyse 2': ('HzH 205', 0, '16:00', 60),
    'Kontrapunkt 1 (vokale Improvisation)': ('HzH 320', 0, '18:30', 60),
    'Satztechniken des 20./21. Jh. 1': ('HzH 204', 2, '14:00', 60),
    'Musikpädagogik 1': ('HzH 316', 3, '13:15', 90),
    'Musikpädagogik 3 (Instrumental- und Gesangspädagogik)': ('PA 311', 1, '11:15', 90),
    'Musik und Gesundheit 1': ('HzH 313', 4, '11:15', 90),
    'Schreibwerkstatt: wissenschaftliches Schreiben': ('HzH 320', 3, '09:15', 90),
    'Einführung in das wissenschaftliche Arbeiten und Schreiben': ('HzH 316', 2, '13:15', 90),
    'Soundtrack der Erinnerung: Musik in den Filmen zum Holocaust': ('HzH 320', 2, '13:15', 90),
    'Hochschulchor': ('Saal Am Palais', 1, '19:00', 135),
}
LATER = {  # courses that start after the first week of lectures: Musikwissenschaft on 12.10., the Hochschulchor on 13.10.
    **dict.fromkeys([
        'Musikgeschichte im Überblick II', 'Musikgeschichte im Überblick III', '„Pour la fin du temps“ – Jüdisch-christliche Endzeitvorstellungen in der Musik',
        'Schreibwerkstatt: wissenschaftliches Schreiben', 'Einführung in das wissenschaftliche Arbeiten und Schreiben',
        'Soundtrack der Erinnerung: Musik in den Filmen zum Holocaust',
    ], date(2026, 10, 12)),
    'Hochschulchor': date(2026, 10, 13),
}
PHASES = {'Hochschulorchester': ('Festsaal', date(2026, 11, 2), 5, '10:00', 420)}  # project weeks instead of weekly rehearsals: room, first day, days, start, minutes

TEACHERS = [  # key, title, first, last, Hauptfach taught
    ('wendt', 'Prof.', 'Katharina', 'Wendt', 'Violine'), ('sauer', 'Prof.', 'Michael', 'Sauer', 'Violine'),
    ('morozova', 'Prof.', 'Elena', 'Morozova', 'Viola'), ('reinholdt', 'Prof.', 'Thomas', 'Reinholdt', 'Violoncello'),
    ('petersen', '', 'Jan', 'Petersen', 'Kontrabass'), ('dubois', 'Prof.', 'Claire', 'Dubois', 'Harfe'),
    ('lindqvist', 'Prof.', 'Anna', 'Lindqvist', 'Flöte'), ('keller', 'Prof.', 'Martin', 'Keller', 'Oboe'),
    ('albers', 'Prof.', 'Stefan', 'Albers', 'Klarinette'), ('brenner', '', 'Ruth', 'Brenner', 'Fagott'),
    ('hallmann', 'Prof.', 'Georg', 'Hallmann', 'Horn'), ('ebert', 'Prof.', 'Lukas', 'Ebert', 'Trompete'),
    ('kroll', '', 'Daniel', 'Kroll', 'Posaune'), ('rehm', '', 'Matthias', 'Rehm', 'Tuba'),
    ('watanabe', 'Prof.', 'Kenji', 'Watanabe', 'Schlagwerk'),
    ('hartung', 'Prof.', 'Sabine', 'Hartung', 'Gesang'), ('lindner', 'Prof.', 'Paul', 'Lindner', 'Gesang'),
    ('varga', 'KS', 'Ilona', 'Varga', 'Gesang'),
    ('feldhaus', 'Prof.', 'Marius', 'Feldhaus', 'Klavier'), ('arai', 'Prof.', 'Yoko', 'Arai', 'Klavier'),
    ('brandauer', 'Prof. Dr.', 'Felix', 'Brandauer', None), ('roth', 'Dr.', 'Miriam', 'Roth', None),
    ('voss', 'Prof. Dr.', 'Henrik', 'Voss', None), ('schulte', '', 'Mira', 'Schulte', None),
    ('engel', '', 'Tobias', 'Engel', None), ('neumann', 'Prof. Dr.', 'Claudia', 'Neumann', None),
    ('ferreira', 'Prof.', 'Nicolás', 'Ferreira', None), ('falk', 'Prof.', 'Regina', 'Falk', None),
    ('weber', 'Prof.', 'Jonas', 'Weber', None), ('popescu', '', 'Alina', 'Popescu', None),
    ('hein', '', 'Wolfgang', 'Hein', None), ('martens', '', 'Lena', 'Martens', None),
    ('scholz', 'Dr.', 'Katrin', 'Scholz', None), ('kessler', 'Dr.', 'Jana', 'Kessler', None),
    ('bellini', 'Dr.', 'Paola', 'Bellini', None), ('hagen', 'Prof.', 'Ulrike', 'Hagen', None),
    ('brueck', 'Prof.', 'Johannes', 'Brück', None), ('sander', 'Prof.', 'Christoph', 'Sander', 'Orgel'),
    ('okafor', 'Prof.', 'Ada', 'Okafor', 'Jazz-Gesang'), ('wiegand', 'KMD', 'Peter', 'Wiegand', None), ('hausmann', 'Prof. Dr.', 'Eva', 'Hausmann', None),
]
STAFF = [  # username, title, first, last, office
    ('asa', '', 'Sabine', 'Krüger', 'ASA'),
    ('pruefungsamt', '', 'Thomas', 'Albrecht', 'Prüfungsamt'),
    ('dekanat', 'Prof. Dr.', 'Johanna', 'Brandt', 'Dekanat'),
    ('veranstaltungsbuero', '', 'Michael', 'Görlitz', 'Veranstaltungsbüro'),
]
LECTURERS = {  # course title (or its start) → teacher key
    'Musikgeschichte im Überblick I': 'brandt', 'Musikgeschichte im Überblick II': 'brandt', 'Musikgeschichte im Überblick III': 'voss',
    'Musikgeschichte im Überblick IV': 'voss', '„Pour la fin du temps“': 'voss', 'Kontrapunkt': 'roth', 'Formenlehre': 'brandauer',
    'Satztechniken': 'roth', 'Allgemeine Musiklehre': 'brandauer', 'Gehörbildung': 'roth', 'Instrumentenkunde': 'brandauer',
    'Höranalyse': 'brandauer', 'Werkanalyse 2': 'roth', 'Musikpädagogik': 'neumann', 'Fachdidaktik Gesang': 'hartung',
    'Fachdidaktik': 'neumann', 'Lehrpraxis': 'neumann', 'Unterrichtspraktisches Klavierspiel': 'schulte', 'Rhetorik': 'kessler',
    'Rhythmik': 'neumann', 'Elementare Musikpädagogik': 'neumann', 'Hochschulchor': 'hagen', 'Hochschulorchester': 'ferreira',
    'Kammermusik': 'reinholdt', 'Orchesterstudien Streicher': 'sauer', 'Orchesterstudien Bläser': 'hallmann', 'Probespieltraining': 'ferreira',
    'Kammerorchester': 'sauer', 'Historische': 'brueck', 'Historisches Instrument': 'brueck', 'Ensemble für Alte Musik': 'brueck',
    'Liszt Brass Ensemble': 'ebert', 'Musik und Gesundheit': 'scholz', 'Alexander-Technik': 'scholz', 'Konditionstraining': 'scholz',
    'Auftrittstraining': 'scholz', 'Mentales Training': 'scholz', 'Selbstmanagement': 'scholz', 'Schreibwerkstatt': 'brandt',
    'Einführung in das wissenschaftliche': 'brandt', 'Brasilianische': 'weber', 'Musikalische Wirkungsforschung': 'weber', 'Soundtrack': 'voss',
    'Bachelorprojekt': 'brandauer', 'Masterprojekt': 'wendt', 'Repertoirekunde Musiktheater': 'varga', 'Repertoirekunde Lied': 'lindner',
    'Italienisch': 'bellini', 'Körpererfahrung': 'falk', 'Grundlagen Schauspiel': 'falk', 'Grundlagen Bühnensprechen': 'kessler',
    'Opernpartien szenisch': 'falk', 'Opernpartien': 'hein', 'Szenische Produktion': 'falk', 'Ensemblesingen': 'hagen',
    'Phonetik': 'bellini', 'Grundlagen Phonetik': 'bellini', 'Aufführungspraxis des Rezitativs': 'varga', 'Liedgestaltung': 'lindner',
    'Einführung Berufspraxis': 'scholz', 'Anatomie': 'hartung', 'Konzert- und Theaterpraxis': 'varga', 'Künstlerische Liedgestaltung': 'lindner',
    'Literaturkunde': 'engel', 'Klavier im 20./21.': 'arai', 'Instrumentale Improvisation': 'weber', 'Künstlerische Projektarbeit': 'feldhaus',
    'Einführung in die Korrepetitionspraxis': 'hein', 'Klavierauszugspiel': 'hein', 'Improvisation': 'weber', 'Geschichte des Klavierliedes': 'voss',
    'Tonstudiotechnik': 'weber', 'Einführung in die Musikvermittlung': 'neumann', 'Notationsgeschichte': 'voss', 'Historische Satzlehre': 'roth',
    'Zweitinstrument': 'morozova', 'Musikpädagogisches Orientierungspraktikum': 'neumann',
    'Chorleitung': 'hagen', 'Grundlagen Dirigieren': 'ferreira', 'Grundlagen Bandleitung': 'weber', 'Stimmbildung': 'hartung', 'Instrumentenpool': 'neumann',
    'Stimmphysiologie': 'scholz', 'Schulpraktisches Gruppenmusizieren': 'neumann', 'Musik und Performance': 'falk', 'Begleitseminar Praxissemester': 'neumann',
    'Musikpädagogik: Forschungskolloquium': 'neumann', 'Liturgik': 'wiegand', 'Gregorianik': 'wiegand', 'Hymnologie': 'wiegand', 'Liturgisches Orgelspiel': 'sander',
    'Orgelexkursion': 'sander', 'Orchesterleitung': 'ferreira', 'Doktorandenkolloquium': 'voss', 'Proben und Aufführungen': 'falk',
}
ORIGINS = [  # weight, first names, last names, nationality, places
    (11, ['Anna', 'Lukas', 'Clara', 'Jakob', 'Paul', 'Lea', 'Nora', 'Felix', 'Hannah', 'Emil', 'Ida', 'Leon', 'Greta', 'Moritz', 'Svea', 'Oskar', 'Maja', 'Frieda', 'Vincent', 'Marlene'],
     ['Müller', 'Schmidt', 'Fischer', 'Hoffmann', 'Lehmann', 'Wagner', 'Richter', 'Klein', 'Wolf', 'Schröder', 'Schwarz', 'Zimmermann', 'Braun', 'Hartmann', 'Lange', 'Werner', 'Krause', 'Meier', 'Köhler', 'Herrmann', 'König', 'Fuchs', 'Peters', 'Möller', 'Jung', 'Hahn', 'Vogel', 'Friedrich', 'Günther', 'Winkler', 'Lorenz', 'Baumann', 'Franke', 'Schuster', 'Ludwig', 'Böhm', 'Winter', 'Seidel', 'Haas', 'Dietrich', 'Ziegler', 'Kuhn', 'Pohl', 'Busch', 'Bergmann', 'Voigt', 'Arnold', 'Pfeiffer'],
     'deutsch', ['Weimar', 'Erfurt', 'Jena', 'Leipzig', 'Dresden', 'Berlin', 'Hamburg', 'München', 'Köln', 'Halle (Saale)', 'Gotha', 'Kassel', 'Nürnberg']),
    (2, ['Mei Lin', 'Yuxuan', 'Hao', 'Xinyi', 'Zihan', 'Jiayi'], ['Chen', 'Wang', 'Li', 'Zhang', 'Liu', 'Zhao'], 'chinesisch', ['Shanghai', 'Peking', 'Chengdu', 'Wuhan']),
    (2, ['Hyun-woo', 'Ji-ho', 'Seo-yeon', 'Min-jun', 'Ha-eun'], ['Park', 'Kim', 'Lee', 'Choi', 'Yoon'], 'südkoreanisch', ['Seoul', 'Busan', 'Daegu']),
    (1, ['Yuki', 'Aiko', 'Kaito', 'Haruka', 'Ren'], ['Tanaka', 'Sato', 'Suzuki', 'Takahashi', 'Ito'], 'japanisch', ['Tokio', 'Osaka', 'Nagoya']),
    (1, ['Zofia', 'Tomasz', 'Marta', 'Jakub', 'Aleksandra'], ['Nowak', 'Mazur', 'Krawczyk', 'Kaczmarek', 'Wójcik'], 'polnisch', ['Krakau', 'Warschau', 'Breslau']),
    (1, ['Chiara', 'Matteo', 'Valentina', 'Lorenzo', 'Giulia'], ['Rossi', 'Bianchi', 'Romano', 'Ricci', 'Marino'], 'italienisch', ['Mailand', 'Rom', 'Turin', 'Bologna']),
    (1, ['Daniil', 'Sofiia', 'Anastasiia', 'Artem', 'Kateryna'], ['Shevchenko', 'Bondarenko', 'Kovalenko', 'Tkachenko', 'Melnyk'], 'ukrainisch', ['Kyjiw', 'Lwiw', 'Odesa']),
]
REPERTOIRE = {
    'strings': ['J. S. Bach: zwei Sätze aus einer Suite oder Partita für {i} solo', 'Ein Konzert der Klassik für {i}, 1. Satz mit Kadenz',
                'Ein virtuoses Werk der Romantik', 'Ein Werk nach 1950', 'Eine Sonate für {i} und Klavier, ein schneller Satz'],
    'winds': ['W. A. Mozart: Konzert für {i}, 1. und 2. Satz', 'Ein Werk der Romantik für {i} und Klavier', 'Ein Werk nach 1950',
              'Orchesterstellen nach Liste der Hochschule', 'Eine Etüde nach Wahl'],
    'voice': ['Eine Arie aus einer Oper von W. A. Mozart', 'Ein Lied der Romantik in deutscher Sprache', 'Ein Lied in französischer oder englischer Sprache',
              'Eine Arie aus Oratorium oder Kantate', 'Ein Werk nach 1950'],
    'piano': ['J. S. Bach: Präludium und Fuge aus dem Wohltemperierten Klavier', 'Eine Sonate der Wiener Klassik, ein schneller Satz',
              'F. Chopin: eine Etüde aus op. 10 oder op. 25', 'Ein Werk der Romantik', 'Ein Werk nach 1950'],
}
NOTES = [Decimal(n) for n in ('1.0', '1.3', '1.7', '2.0', '2.3', '2.7', '3.0', '3.3', '3.7', '4.0')]
ARTISTIC = ('Künstlerisch-praktische', 'Rezital', 'Künstlerische Präsentation')


def handle(first, last):
    """Mail-safe login name: 'Nicolás', 'Ferreira' → 'nicolas.ferreira'."""
    name = f'{first}.{last}'.lower().replace(' ', '-')
    for a, b in [('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss'), ('á', 'a'), ('ó', 'o'), ('ś', 's'), ('ł', 'l')]:
        name = name.replace(a, b)
    return name


def family(subject):
    return 'strings' if subject in STRINGS else 'winds' if subject in WINDS else 'voice' if subject in ('Gesang', 'Jazz-Gesang') else 'piano'


def at(day, minutes=0, hour=None):
    """Aware local datetime on `day` at `hour` o'clock or `minutes` after midnight."""
    minutes = hour * 60 if hour is not None else minutes
    return timezone.make_aware(datetime(day.year, day.month, day.day, minutes // 60, minutes % 60))


def clock(text):
    hours, minutes = text.split(':')
    return int(hours) * 60 + int(minutes)


def length(form):
    """Minutes of one weekly unit from the plan's form, e.g. 'E 90' → 90."""
    found = re.search(r'\d+', form or '')
    return int(found.group()) if found else 90


def sws(form):
    """Semesterwochenstunden as the HfM counts them: 60 minutes in artistic lessons, 45 in academic courses."""
    return (Decimal(length(form)) / (60 if form.startswith('E') else 45)).quantize(Decimal('0.1'))


def lecturer(title):
    return next((key for start, key in sorted(LECTURERS.items(), key=lambda kv: -len(kv[0])) if title.startswith(start)), 'brandauer')


class Command(BaseCommand):
    help = 'Fill an empty database with demo data (fictional people, HfM Weimar structure). Password for every account: demo.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Empty the database first.')

    def handle(self, *args, reset, **options):
        if reset:
            call_command('flush', interactive=False, verbosity=0)
        elif Person.objects.exists():
            raise CommandError('The database is not empty. Use --reset to replace everything.')
        self.rng = random.Random(1872)  # the year the Hochschule was founded
        self.password = make_password('demo')
        self.names = set()
        with transaction.atomic(), history.quietly():
            self.terms()
            self.structure()
            self.people()
            self.students()
            self.teaching()
            self.guests()
            self.exams()
            self.admissions()
            self.practice()
            self.events()
            self.letters()
        self.changes()
        self.stdout.write(self.style.SUCCESS(
            f'{Person.objects.count()} Personen, {Module.objects.count()} Planeinträge, {Course.objects.count()} Veranstaltungen, '
            f'{Participation.objects.count()} Teilnahmen, {Booking.objects.count()} Buchungen.'
        ))

    # Calendar, plans, rooms

    def terms(self):
        terms = []
        for year in range(2022, 2028):
            april, name = date(year, 4, 1), f'SoSe {year}'
            lectures = LECTURES.get(name, (april + timedelta(days=(2 - april.weekday()) % 7 + 7), date(year, 7, 13)))
            terms.append(Term(name=name, start=april, end=date(year, 9, 30), lectures_start=lectures[0], lectures_end=lectures[1], deadline=date(year, 1, 18), fee=FEES.get(name, FEE)))
            if year < 2027:
                october, february, name = date(year, 10, 1), date(year + 1, 2, 1), f'WiSe {year}/{(year + 1) % 100:02d}'
                lectures = LECTURES.get(name, (october + timedelta(days=-october.weekday() % 7), february + timedelta(days=(4 - february.weekday()) % 7)))
                terms.append(Term(name=name, start=october, end=date(year + 1, 3, 31), lectures_start=lectures[0], lectures_end=lectures[1], deadline=date(year, 6, 15), fee=FEES.get(name, FEE)))
        for term in terms:
            term.reregistration_start = term.deadline - timedelta(days=14)  # Rückmeldung SoSe 2027: 04.–18.01.2027
        Term.objects.bulk_create(terms)
        self.calendar = list(Term.objects.order_by('start'))
        self.now = next(i for i, t in enumerate(self.calendar) if t.name == 'WiSe 2026/27')
        self.current, self.next = self.calendar[self.now], self.calendar[self.now + 1]

    def structure(self):
        self.institutes = {key: Institute.objects.create(name=name) for key, name in INSTITUTES.items()}
        self.subjects = {
            name: Subject.objects.create(name=name, institute=self.institutes[SUBJECT_INSTITUTE[name]])
            for name in STRINGS + WINDS + ['Gesang', 'Jazz-Gesang', 'Klavier', 'Orgel'] + RESEARCH
        }
        self.programs, self.teach, self.first_course = {}, {}, {}
        for name, degree, version, semesters, subjects, plan in PROGRAMS:
            program = Program.objects.create(name=name, degree=degree, version=version, semesters=semesters)
            program.subjects.set(self.subjects[s] for s in subjects)
            self.build(program, plan)
            self.programs[(degree, name)] = program
        self.titles = dict(Module.objects.values_list('pk', 'title'))
        self.bmsh = self.programs[('B.Mus.', 'Streichinstrumente und Harfe')]
        self.bmbs = self.programs[('B.Mus.', 'Blasinstrumente und Schlagwerk')]
        self.bmgm = self.programs[('B.Mus.', 'Gesang | Musiktheater')]
        self.bmkl = self.programs[('B.Mus.', 'Klavier')]
        self.mm = self.programs[('M.Mus.', 'Streichinstrumente')]
        self.guest = self.programs[('Gasthörerschaft', 'ohne Abschluss')]
        self.rooms = {}
        for building, entries in ROOMS.items():
            for entry in entries.split('; '):
                name, purpose = entry.split(':')
                function, capacity, equipment = furnish(name, purpose)
                self.rooms[name] = Room.objects.create(building=building, name=name, purpose=purpose, function=function, capacity=capacity, equipment=equipment)
        for key, names in ROOM_INSTITUTES.items():
            self.institutes[key].rooms.set(self.rooms[name] for name in names.split(', '))

    def build(self, program, nodes, parent=None):
        """Store a plan as the module tree; remember for each piece of course work which course teaches it."""
        for node in nodes:
            shape = node['shape']
            if shape == 'L':
                leaf = Module.objects.create(program=program, parent=parent, code=node['code'], title=node['title'], credits=node['credits'], description=node['form'])
                self.teach[leaf.pk] = (node['course'], 1, node['form'], None)
                continue
            module = Module.objects.create(
                program=program, parent=parent, code=node['code'], title=node['title'], credits=node['credits'],
                weight=Decimal(str(node['weight'])), description=node['note'],
            )
            first, n = [], 0
            for part in node['parts']:
                if part['shape'] in ('M', 'G', 'L'):
                    self.build(program, [part], module)
                    continue
                if part['shape'] == 'X':
                    n += 1
                    Module.objects.create(
                        program=program, parent=module, code=f"{node['code']}.{n:02d}", title=part['title'], credits=0,
                        semester=part['semester'], exam=True, weight=Decimal(str(part['weight'])), description=part['form'],
                    )
                    first.append(part['semester'])
                    continue
                for k, (semester, credits) in enumerate(sorted(part['credits'].items()), part['start']):
                    n += 1
                    leaf = Module.objects.create(
                        program=program, parent=module, code=f"{node['code']}.{n:02d}", title=part['title'], credits=credits,
                        semester=semester, description=part['form'],
                    )
                    self.teach[leaf.pk] = (part['course'], k, part['form'], part['taught'])
                    self.first_course.setdefault(module.pk, part['course'] if isinstance(part['course'], str) else part['course']['WiSe'])
                    first.append(semester)
            if first:
                module.semester = min(first)
                module.save(update_fields=['semester'])

    # People

    def person(self, username, first, last, title='', email=None, **fields):
        return Person.objects.create(
            username=username, first_name=first, last_name=last, title=title, password=self.password,
            email=email or f'{handle(first, last)}@hfm-weimar.de', **fields,
        )

    def people(self):
        Person.objects.create(username='admin', first_name='System', last_name='Administration', email='campus@hfm-weimar.de', password=self.password, is_staff=True, is_superuser=True)
        self.staff = {}
        groups = {name: Group.objects.create(name=name) for name in OFFICES}
        rights = {'ASA': ['core', 'students'], 'Prüfungsamt': ['courses'], 'Dekanat': ['curriculum', 'courses'], 'Veranstaltungsbüro': ['rooms']}
        for name, labels in rights.items():
            groups[name].permissions.set(Permission.objects.filter(content_type__app_label__in=labels))
        for username, title, first, last, office in STAFF:
            self.staff[username] = person = self.person(username, first, last, title, is_staff=True)
            person.groups.add(groups[office])
        self.teachers, self.by_subject = {'brandt': self.staff['dekanat']}, defaultdict(list)
        self.staff['dekanat'].institute = self.institutes['MW']
        self.staff['dekanat'].save(update_fields=['institute'])
        for key, title, first, last, subject in TEACHERS:
            institute = self.institutes[SUBJECT_INSTITUTE[subject] if subject else TEACHER_INSTITUTE[key]]
            self.teachers[key] = self.person('lehre' if key == 'wendt' else handle(first, last), first, last, title, institute=institute)
            if subject:
                self.by_subject[subject].append(self.teachers[key])
        self.names |= {(p.first_name, p.last_name) for p in Person.objects.all()}

    def newcomer(self, username=None, first=None, last=None, origin=None, **fields):
        rng = self.rng
        weight, firsts, lasts, nationality, places = origin or rng.choices(ORIGINS, weights=[o[0] for o in ORIGINS])[0]
        while not first or (first, last) in self.names:
            first, last = rng.choice(firsts), rng.choice(lasts)
        self.names.add((first, last))
        name, gender = handle(first, last), 'w' if first in FEMALE else 'm'
        born = date(rng.randint(1997, 2007), rng.randint(1, 12), rng.randint(1, 28))
        german, talent = nationality == 'deutsch', born.year == 2007 and rng.random() < 0.5  # admitted for artistic talent, before the Abitur
        return self.person(
            username or name, first, last, email=f'{name}@stud.hfm-weimar.de', gender=gender,
            given_names=f'{first} {rng.choice(MIDDLE[gender])}' if german and rng.random() < 0.3 else '',
            birth_name=rng.choice(lasts) if born.year < 2000 and gender == 'w' and rng.random() < 0.5 else '',
            birth_date=born, birth_place=rng.choice(places), nationality=nationality,
            second_nationality='deutsch' if not german and rng.random() < 0.15 else '',
            entrance=Person.Entrance.TALENT if talent else Person.Entrance.GENERAL if german else Person.Entrance.FOREIGN,
            entrance_date=None if talent else date(born.year + 19, 6, rng.randint(20, 30)),
            entrance_place='' if talent else f'{rng.choice(places)}, Deutschland' if german else COUNTRIES[nationality],
            address=f'{rng.choice(["Marienstraße", "Steubenstraße", "Trierer Straße", "Humboldtstraße", "Bauhausstraße"])} {rng.randint(1, 60)}\n99423 Weimar',
            **fields,
        )

    def panel(self, subject, size=3):
        """Examiners: the subject's teachers first, then colleagues of the same family."""
        own = self.by_subject[subject]
        others = [t for s, ts in self.by_subject.items() if family(s) == family(subject) and s != subject for t in ts]
        return (own + self.rng.sample(others, len(others)))[:size]

    # Students and what they have done

    def students(self):
        rng = self.rng
        self.enrolled, self.ability, self.fs = [], {}, {}
        cohorts = [(self.bmsh, s, n) for s, n in zip(STRINGS, (9, 4, 5, 3, 2))]
        cohorts += [(self.bmbs, s, n) for s, n in zip(WINDS, (3, 2, 3, 2, 3, 3, 2, 1, 2))]
        cohorts += [(self.bmgm, 'Gesang', 10), (self.bmkl, 'Klavier', 8), (self.mm, 'Violine', 3), (self.mm, 'Viola', 1), (self.mm, 'Violoncello', 2)]
        chen = next(o for o in ORIGINS if o[3] == 'chinesisch')
        self.demo = self.newcomer('student', 'Mei Lin', 'Chen', chen)
        for program, subject, count in cohorts:
            for n in range(count):
                if program == self.bmsh and subject == 'Violine' and n == 0:
                    self.enrol(self.demo, program, subject, 5)
                else:
                    self.enrol(self.newcomer(), program, subject, rng.choice([1, 2] if program == self.mm else [1, 1, 2, 3, 3, 4, 5, 5, 6, 7, 7, 8]))
        # Lehramt, Kirchenmusik and the postgraduate programmes: degree, programme, subject, Fachsemester, focus, supervisor
        for degree, name, subject, semester, focus, teacher in [
            ('B.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Klavier', 1, None, None), ('B.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Gesang', 3, None, None),
            ('B.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Jazz-Gesang', 3, None, None), ('B.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Violine', 1, None, None),
            ('B.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Trompete', 3, None, None), ('B.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Flöte', 1, None, None),
            ('M.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Klavier', 2, 'ME-PR-03', None), ('M.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Gesang', 2, 'ME-PR-04', None),
            ('M.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Jazz-Gesang', 1, 'ME-PR-05', None), ('M.Ed.', 'Musik an Gymnasien (Doppelfach)', 'Violine', 1, 'ME-PR-01', None),
            *[('Staatsexamen', 'Lehramt an Gymnasien, Studienfach Musik', s, n, None, None) for s, n in [('Gesang', 1), ('Klavier', 3), ('Violoncello', 3), ('Jazz-Gesang', 1)]],
            ('Diplom', 'Kirchenmusik A', 'Orgel', 3, 'KM-IV-EV', None), ('Diplom', 'Kirchenmusik A', 'Orgel', 5, 'KM-IV-KA', None),
            ('Konzertdiplom', 'Konzertexamen', 'Klavier', 2, None, None), ('Konzertdiplom', 'Konzertexamen', 'Violine', 3, None, None),
            ('Dr. phil.', 'Promotion', 'Musikwissenschaft', 3, None, 'voss'), ('Dr. phil.', 'Promotion', 'Musikwissenschaft', 5, None, 'voss'),
            ('Dr. phil.', 'Promotion', 'Musikpädagogik', 2, None, 'neumann'), ('Dr. phil.', 'Promotion', 'Kulturmanagement', 1, None, 'hausmann'),
            *[('Zertifikat', 'Thüringer Opernstudio', 'Gesang', 1, None, None)] * 3,
        ]:
            program = self.programs[(degree, name)]
            focus = program.modules.get(code=focus) if focus else None
            self.enrol(self.newcomer(), program, subject, semester, focus=focus, teacher=self.teachers.get(teacher))
        organist = self.newcomer()  # studies Kirchenmusik and Klavier side by side
        self.enrol(organist, self.programs[('Diplom', 'Kirchenmusik A')], 'Orgel', 7, focus=self.programs[('Diplom', 'Kirchenmusik A')].modules.get(code='KM-IV-EV'))
        self.enrol(organist, self.bmkl, 'Klavier', 5)

    def enrol(self, person, program, subject, semester, focus=None, teacher=None):
        rng = self.rng
        start = self.now - semester + 1
        teacher = teacher or (self.teachers['wendt'] if person == self.demo else rng.choice(self.by_subject[subject]))
        study = Study.objects.create(
            person=person, program=program, subject=self.subjects[subject], start_term=self.calendar[start],
            status=Study.Status.ENROLLED, since=self.calendar[start].start, teacher=teacher, focus=focus,
        )
        if not person.matriculation:  # Matrikelnummer and health insurance came with the first enrolment
            person.matriculation = str(100000 + person.pk)
            person.insurer, person.insurance = rng.choice(INSURERS), Person.Insurance.STATUTORY if rng.random() < 0.9 else Person.Insurance.EXEMPT
            person.insurance_confirmed = self.calendar[start].start - timedelta(days=rng.randint(20, 50))
            if program.degree not in ('B.Mus.', 'B.Ed.', 'Staatsexamen', 'Diplom'):
                person.external_terms = rng.choice([6, 8, 10])  # earlier degree elsewhere
            person.save(update_fields=['matriculation', 'insurer', 'insurance', 'insurance_confirmed', 'external_terms'])
        StudyTerm.objects.bulk_create(  # paid at enrolment, then within each Rückmeldung period
            StudyTerm(study=study, term=term, paid=(term.start - timedelta(days=rng.randint(20, 60))) if k == 0 else term.deadline - timedelta(days=rng.randint(0, 14)))
            for k, term in enumerate(self.calendar[start:start + semester])
        )
        study.fs = self.fs[study.pk] = semester
        self.ability[study.pk] = 1.6 if person == self.demo else rng.uniform(1.0, 2.6)
        self.enrolled.append(study)

    def plan(self, study):
        """The leaves this study takes: everything required, and one pick wherever the plan offers a choice."""
        rng, chosen = self.rng, []

        def walk(nodes):
            for node in nodes:
                if not node.nodes:
                    chosen.append(node)
                    continue
                kids = node.nodes
                if study.focus_id in {kid.pk for kid in kids}:  # the chosen Schwerpunkt or Profil
                    kids = [kid for kid in kids if kid.pk == study.focus_id]
                elif node.offered > node.credits:
                    if all(kid.credits >= node.credits for kid in kids):  # KV or IV, variant A, B or C: mostly the first
                        kids = [kids[0] if rng.random() < 0.75 else rng.choice(kids)]
                    else:
                        picked, total = [], 0
                        for kid in rng.sample(kids, len(kids)):
                            if total >= node.credits:
                                break
                            picked.append(kid)
                            total += kid.credits
                        kids = picked
                walk(kids)

        walk(study.program.curriculum())
        open_ = [m for m in chosen if m.semester is None]
        for i, m in enumerate(open_):
            m.semester = 1 + i * study.program.semesters // len(open_)
        return chosen

    def grade(self, study, spread=0.35):
        value = self.ability[study.pk] + self.rng.gauss(0, spread)
        return min(NOTES, key=lambda n: abs(float(n) - value))

    def course(self, term, title, kind=Course.Kind.COURSE, **fields):
        key = (term.pk, title)
        if key not in self.courses:
            self.courses[key] = Course.objects.create(term=term, title=title, kind=kind, **fields)
            self.courses[key].form = ''
        return self.courses[key]

    def course_for(self, study, leaf, term):
        """(course, teacher, lesson rooms) in which `study` does this piece of course work in `term`."""
        spec, number, form, taught = self.teach[leaf.pk]
        title = (spec[term.name[:4]] if isinstance(spec, dict) else spec).format(n=number, r=ROMAN[number])
        subject = study.subject.name
        if taught == HF:
            label = 'Gesang' if subject == 'Gesang' else f'Hauptfach {subject}'
            course = self.course(term, f'{label} · Klasse {study.teacher.last_name}', Course.Kind.LESSON, subject=study.subject, hours=sws(form))
            return course, study.teacher, LESSON_ROOMS[subject]
        if taught in (WS, PIANO, SPEECH):
            teacher = self.teachers[{WS: ['popescu', 'hein', 'martens'][study.pk % 3], PIANO: ['schulte', 'engel'][study.pk % 2], SPEECH: 'kessler'}[taught]]
            label = {WS: 'Werkstudium', PIANO: 'Klavier (Pflichtfach)', SPEECH: 'Bühnensprechen'}[taught]
            return self.course(term, f'{label} · {teacher.last_name}', Course.Kind.LESSON, hours=sws(form)), teacher, LESSON_ROOMS[taught]
        course = self.course(term, title, hours=sws(form) if form else None, deadline=date(2026, 10, 16) if term == self.current else None)
        course.form = form
        return course, self.teachers[lecturer(title)], None

    def teaching(self):
        """Past results that make up each transcript, and this term's participations, lessons and bookings."""
        rng = self.rng
        self.courses, links, staff, rows, scores = {}, set(), set(), {}, []
        self.lessons, booked = [], set()  # weekly one-to-one lessons this term: (person, course, teacher, rooms, minutes)
        for study in self.enrolled:
            subject = study.subject.name
            for leaf in self.plan(study):
                if leaf.semester > study.fs:
                    continue
                term = self.calendar[self.now - study.fs + leaf.semester]
                done = leaf.semester < study.fs
                if leaf.exam:
                    if not done or leaf.description == f'entfällt für {subject}' or (study.person == self.demo and leaf.code.startswith('BM-XX-MW-01')):
                        continue  # this term's exams are arranged in exams(); the demo student still owes Musikgeschichte
                    artistic = leaf.title.startswith(ARTISTIC)
                    name = f'{self.titles[leaf.parent_id]} · {leaf.title}' + (f' · {subject}' if artistic else '')
                    course = self.course(term, name, Course.Kind.EXAM, subject=study.subject if artistic else None, released=at(term.end - timedelta(days=45), hour=12))
                    examiners = self.panel(subject) if artistic else [self.teachers[lecturer(self.first_course.get(leaf.parent_id, ''))]]
                    grade = self.grade(study)
                    rows[(course.pk, study.pk, leaf.pk)] = Participation(course=course, study=study, module=leaf, grade=grade, passed=True, created=at(term.start, hour=9))
                    for examiner in examiners:
                        target = float(grade) + rng.gauss(0, 0.25)
                        scores.append((course.pk, study.pk, leaf.pk, examiner, min(NOTES, key=lambda n: abs(float(n) - target))))
                    staff |= {(course.pk, e.pk) for e in examiners}
                elif self.teach[leaf.pk][0] == JENA:  # taught in Jena, credited here once done
                    if done:
                        jena = self.course(term, f'Anerkennungen {term}', Course.Kind.RECOGNITION, released=at(term.end - timedelta(days=30), hour=12))
                        rows[(jena.pk, study.pk, leaf.pk)] = Participation(
                            course=jena, study=study, module=leaf, passed=True, note='Friedrich-Schiller-Universität Jena', created=at(term.end - timedelta(days=30), hour=10),
                        )
                    continue
                else:
                    course, teacher, rooms = self.course_for(study, leaf, term)
                    staff.add((course.pk, teacher.pk))
                    rows[(course.pk, study.pk, leaf.pk)] = Participation(
                        course=course, study=study, module=leaf, passed=True if done else None,
                        created=at(term.start + timedelta(days=rng.randint(0, 14)), hour=rng.randint(8, 20)),
                    )
                    if not done and rooms and (study.person.pk, course.pk) not in booked:
                        booked.add((study.person.pk, course.pk))
                        self.lessons.append((study.person, course, teacher, rooms, length(self.teach[leaf.pk][2])))
                links.add((course.pk, leaf.pk))
        created = {(p.course_id, p.study_id, p.module_id): p for p in Participation.objects.bulk_create(rows.values())}
        electives = Course.objects.filter(term=self.current, title__in=['Alexander-Technik', 'Auftrittstraining', 'Mentales Training', 'Selbstmanagement', 'Kammerorchester'])
        full = electives.annotate(size=models.Count('participations')).order_by('-size').first()
        if full:  # an over-subscribed course shows the waitlist
            full.capacity = max(1, full.size - 2)
            full.save(update_fields=['capacity'])
        Score.objects.bulk_create(Score(participation=created[(c, s, m)], examiner=e, value=v) for c, s, m, e, v in scores)
        Course.staff.through.objects.bulk_create([Course.staff.through(course_id=c, person_id=p) for c, p in staff], ignore_conflicts=True)
        Course.modules.through.objects.bulk_create([Course.modules.through(course_id=c, module_id=m) for c, m in links])
        self.schedule()

    def guests(self):
        """Gasthörer:innen in this term's lectures, and the snags the Rückmeldung has to show."""
        lectures = Course.objects.filter(term=self.current, title__in=['Musikgeschichte im Überblick II', 'Musik und Gesundheit 1', 'Instrumentenkunde/Akustik'])
        for n, course in enumerate(lectures):
            person = self.newcomer(origin=ORIGINS[0])
            study = Study.objects.create(
                person=person, program=self.guest, subject=self.subjects['Musikwissenschaft'], start_term=self.current,
                status=Study.Status.GUEST, since=self.current.start + timedelta(days=7 + n),
            )
            Participation.objects.create(course=course, study=study, created=at(study.since, hour=10))
        others = [s.person for s in self.enrolled if s.person != self.demo]
        for person, hold in zip(self.rng.sample(others, 2), ['Rückgabe von Leihinstrumenten ausstehend', 'Bibliotheksgebühren offen']):
            person.hold = hold
            person.save(update_fields=['hold'])
        for person in self.rng.sample(others, 2):  # changed insurer; the new one has not reported yet
            person.insurer, person.insurance_confirmed = 'BARMER' if person.insurer != 'BARMER' else 'AOK PLUS', None
            person.save(update_fields=['insurer', 'insurance_confirmed'])

    def schedule(self):
        """Weekly bookings across the lecture period: one-to-one lessons first, then group courses; nothing overlaps."""
        rng, busy, patterns = self.rng, defaultdict(list), []

        def free(keys, start, end):
            return all(e <= start or end <= s for key in keys for s, e in busy[key])

        def take(keys, start, end):
            for key in keys:
                busy[key].append((start, end))

        for person, course, teacher, rooms, minutes in self.lessons:
            days = [(teacher.pk + k) % 5 for k in range(5)]
            room, day, start = next(
                (room, day, start) for day in days for start in range(9 * 60, 20 * 60 - minutes + 1, 15) for room in rooms
                if free([(room, day), (teacher.pk, day)], start, start + minutes)
            )
            take([(room, day), (teacher.pk, day)], start, start + minutes)
            patterns.append((room, day, start, minutes, person, course))
        groups = [c for c in self.courses.values() if c.term_id == self.current.pk and c.kind == Course.Kind.COURSE and c.form[:1] in ('V', 'S', 'Ü', 'G')]
        bookings = []
        for course in sorted(groups, key=lambda c: c.title not in FIXED):
            teacher = course.staff.first()
            if course.title in PHASES:
                room, first, days, start, minutes = PHASES[course.title]
                bookings += [
                    Booking(room=self.rooms[room], start=at(first + timedelta(days=n), clock(start)), end=at(first + timedelta(days=n), clock(start) + minutes),
                            person=teacher, course=course, category=Booking.Category.ORCHESTRA)
                    for n in range(days)
                ]
                continue
            if course.title in FIXED:
                room, day, start, minutes = FIXED[course.title]
                start = clock(start)
            else:
                minutes = length(course.form)
                room, day, start = next(
                    (room, day, start) for day in rng.sample(range(5), 5) for start in (9 * 60 + 15, 11 * 60 + 15, 13 * 60 + 15, 15 * 60 + 15, 17 * 60 + 15)
                    for room in SEMINAR_ROOMS if free([(room, day), (teacher.pk, day)], start, start + minutes)
                )
            take([(room, day), (teacher.pk, day)], start, start + minutes)
            patterns.append((room, day, start, minutes, teacher, course))
        for room, day, start, minutes, person, course in patterns:
            first = LATER.get(course.title, self.current.lectures_start)
            series, d = uuid.uuid4(), first + timedelta(days=(day - first.weekday()) % 7)
            while d <= self.current.lectures_end:
                if d not in FREE_DAYS:
                    category = Booking.Category.REHEARSAL if course.title == 'Hochschulchor' else Booking.Category.LESSON
                    bookings.append(Booking(room=self.rooms[room], start=at(d, start), end=at(d, start + minutes), person=person, course=course, series=series, category=category))
                d += timedelta(weeks=1)
        Booking.objects.bulk_create(bookings)
        meeting = timezone.localdate()
        Booking.objects.create(room=self.rooms['FH 115'], start=at(meeting, hour=14), end=at(meeting, 15 * 60 + 30), person=self.staff['dekanat'], title='Prüfungsausschuss', category=Booking.Category.MEETING)

    # This term's exams: registration 01.–15.11.2026, exam period 08.–26.02.2027

    def exams(self):
        rng, now = self.rng, self.current
        taken = set(Participation.objects.filter(course__term=now).values_list('study_id', 'module__parent_id'))
        due = defaultdict(list)
        for study in self.enrolled:
            for leaf in Module.objects.filter(program=study.program, exam=True, semester=study.fs):
                if (study.pk, leaf.parent_id) not in taken or leaf.description == f'entfällt für {study.subject}':  # only exams of modules the study is doing
                    continue
                artistic = leaf.title.startswith(ARTISTIC)
                due[(self.titles[leaf.parent_id], leaf.title, study.subject.name if artistic else '')].append((study, leaf))
        stages, written = ['Festsaal', 'Saal Am Palais', 'FH 106'], ['HzH 313', 'HzH 316', 'HzH 320']
        for n, ((module, title, subject), entries) in enumerate(sorted(due.items())):
            course = Course.objects.create(
                term=now, kind=Course.Kind.EXAM, title=f'{module} · {title}' + (f' · {subject}' if subject else ''),
                subject=self.subjects.get(subject), deadline=date(2026, 11, 15),
            )
            course.staff.set(self.panel(subject) if subject else [self.teachers[lecturer(self.first_course.get(entries[0][1].parent_id, ''))]])
            course.modules.set({leaf for _, leaf in entries})
            start = at(EXAM_DAYS[(n // 3) % len(EXAM_DAYS)], hour=9 + (n % 3) * 3)
            minutes = 30 * len(entries) if subject else 120
            room = self.rooms[(stages if subject else written)[n % 3]]
            if not Booking(room=room, start=start, end=start + timedelta(minutes=minutes)).clashes().exists():
                Booking.objects.create(room=room, start=start, end=start + timedelta(minutes=minutes), person=self.staff['pruefungsamt'], course=course, category=Booking.Category.EXAM)
            Participation.objects.bulk_create(
                Participation(
                    course=course, study=study, module=leaf, slot=start + timedelta(minutes=30 * k if subject else 0),
                    note=self.repertoire(study.subject.name) if subject else '', created=at(date(2026, 11, rng.randint(1, 14)), hour=10),
                )
                for k, (study, leaf) in enumerate(entries)
            )
        demo_study = next(s for s in self.enrolled if s.person == self.demo)
        owed = Module.objects.get(program=demo_study.program, parent__code='BM-XX-MW-01', exam=True)
        if not Course.objects.filter(term=now, modules=owed).exists():  # the exam Mei Lin still owes is on offer this term
            course = Course.objects.create(term=now, kind=Course.Kind.EXAM, title=f'Musikgeschichte I · {owed.title}', deadline=date(2026, 11, 15))
            course.staff.set([self.teachers['voss']])
            course.modules.set(Module.objects.filter(parent__code='BM-XX-MW-01', exam=True))
            start = at(date(2027, 2, 10), hour=10)
            Booking.objects.create(room=self.rooms['HzH 316'], start=start, end=start + timedelta(hours=3), person=self.staff['pruefungsamt'], course=course, category=Booking.Category.EXAM)
        # a resit held last week: two of three marks entered, results not yet released
        failed = list(Participation.objects.filter(
            course__kind=Course.Kind.EXAM, module__parent__code='BM-BS-01', module__semester=4, study__in=[s for s in self.enrolled if s.fs == 5],
        ).select_related('study__subject', 'module')[:2])
        if failed:
            resit = Course.objects.create(term=now, kind=Course.Kind.EXAM, title='Künstlerische Präsentation I · Wiederholungsprüfung', deadline=date(2026, 9, 30))
            examiners = [self.teachers['albers'], self.teachers['keller'], self.teachers['hallmann']]
            resit.staff.set(examiners)
            resit.modules.set({p.module for p in failed})
            start = at(date(2026, 10, 14), hour=10)
            Booking.objects.create(room=self.rooms['FH 106'], start=start, end=start + timedelta(hours=1), person=self.staff['pruefungsamt'], course=resit, category=Booking.Category.EXAM)
            for k, p in enumerate(failed):
                p.grade, p.passed = Decimal('5.0'), False
                p.save(update_fields=['grade', 'passed'])
                Score.objects.filter(participation=p).update(value=Decimal('5.0'))
                retry = Participation.objects.create(course=resit, study=p.study, module=p.module, slot=start + timedelta(minutes=30 * k), note=self.repertoire(p.study.subject.name))
                Score.objects.bulk_create(Score(participation=retry, examiner=e, value=rng.choice(NOTES[1:5])) for e in examiners[:2])

    def repertoire(self, subject):
        return '\n'.join(w.format(i=subject) for w in self.rng.sample(REPERTOIRE[family(subject)], 3))

    # Admissions: the WiSe intake is decided, the SoSe intake is being marked

    def admissions(self):
        rng, now = self.rng, self.current
        outcomes = [(s, 'enrolled') for s in self.enrolled if s.fs == 1 and s.program.degree in ('B.Mus.', 'M.Mus.')]  # the intakes with an Eignungsprüfung here
        for status, count in [(Study.Status.REJECTED, 14), (Study.Status.WAITLISTED, 2), (Study.Status.ADMITTED, 2)]:
            for n in range(count):
                program = rng.choice([self.bmsh, self.bmsh, self.bmbs, self.bmgm, self.bmkl, self.mm])
                subject = rng.choice(list(program.subjects.values_list('name', flat=True)))
                study = Study.objects.create(
                    person=self.newcomer(), program=program, subject=self.subjects[subject], start_term=now, status=status,
                    since=date(2026, 6, rng.randint(10, 20)), teacher=rng.choice(self.by_subject[subject]),
                )
                if status == Study.Status.ADMITTED:  # accepted; one health insurer has reported, the other not yet
                    StudyTerm.objects.create(study=study, term=now)
                    study.person.insurer, study.person.insurance = rng.choice(INSURERS), Person.Insurance.STATUTORY
                    study.person.insurance_confirmed = date(2026, 9, 28) if n == 0 else None
                    study.person.save(update_fields=['insurer', 'insurance', 'insurance_confirmed'])
                outcomes.append((study, status))
        self.intake(now, outcomes, video_deadline=date(2026, 3, 15), audition_day=date(2026, 5, 29), finished=True)
        wanted = [(self.bmsh, 'Violine', 4), (self.bmsh, 'Viola', 2), (self.bmsh, 'Violoncello', 4), (self.bmsh, 'Kontrabass', 1),
                  (self.bmbs, 'Flöte', 3), (self.bmbs, 'Klarinette', 2), (self.bmbs, 'Oboe', 2), (self.bmbs, 'Fagott', 2),
                  (self.mm, 'Violine', 3), (self.mm, 'Violoncello', 1)]  # no SoSe intake for Gesang, Klavier, Horn and the low brass
        self.applicant = self.newcomer('bewerbung', 'Jonas', 'Becker', ORIGINS[0])
        outcomes = []
        for program, subject, count in wanted:
            for n in range(count):
                person = self.applicant if (program, subject, n) == (self.bmsh, 'Violoncello', 0) else self.newcomer()
                study = Study.objects.create(
                    person=person, program=program, subject=self.subjects[subject], start_term=self.next,
                    since=date(2026, 10, rng.randint(1, 20)), teacher=rng.choice(self.by_subject[subject]),
                )
                outcomes.append((study, 'running'))
        self.intake(self.next, outcomes, video_deadline=date(2026, 11, 2), audition_day=date(2027, 1, 13), finished=False)

    def intake(self, term, outcomes, video_deadline, audition_day, finished):
        """Audition courses of one intake and every applicant's way through them."""
        rng, applicant = self.rng, getattr(self, 'applicant', None)
        closed = at(audition_day + timedelta(days=20), hour=12) if finished else None
        written = audition_day + timedelta(days=2)
        written = at(written + timedelta(days=(7 - written.weekday()) % 7 if written.weekday() > 4 else 0), hour=9)  # never on a weekend
        shared = []
        for title, keys, room, step in [
            ('Eignungsprüfung Klavier', ['schulte', 'engel'], 'FH 213', 15),
            ('Eignungsprüfung Musiktheorie und Gehörbildung', ['brandauer', 'roth'], 'HzH 313', 0),
        ]:
            course = Course.objects.create(term=term, kind=Course.Kind.AUDITION, title=title, pass_mark=Decimal('11.6'), released=closed)
            course.staff.set(self.teachers[k] for k in keys)
            course.plan = (written, self.rooms[room], step)
            shared.append(course)
        accompanist = Course.objects.create(term=term, kind=Course.Kind.LESSON, title=f'Korrepetition Eignungsprüfung {term}', hours=Decimal('0.5'))
        accompanist.staff.set([self.teachers['popescu'], self.teachers['hein']])
        rounds, rows, scores, documents = {}, [], [], []

        def sit(study, course, level, jury, note):
            marks = [Decimal(str(round(max(0, min(25, level + rng.gauss(0, 0.9))), 1))) for _ in jury]
            if finished or course.released:
                marked = jury
            elif course.video:  # video round being marked; the Violine jury chair is behind
                marked = [] if study.since.day >= 12 else [e for e in jury if e != self.teachers['wendt'] or study.since.day % 3]
            else:
                marked = []
            p = Participation(course=course, study=study, note=note, created=at(study.since, hour=12))
            if marked and len(marked) == len(jury):
                p.grade = mean(marks)
                p.passed = course.passes(p.grade)
            if hasattr(course, 'plan'):
                begin, _, step = course.plan
                p.slot = begin + timedelta(minutes=step * sum(1 for r in rows if r.course is course))
            rows.append(p)
            scores.extend((p, e, v) for e, v in zip(marked, marks))
            return p

        for study, outcome in outcomes:
            subject, master = study.subject.name, study.program.degree == 'M.Mus.'
            key = (study.program_id, subject)
            if key not in rounds:
                level, n = (' (Master)' if master else ''), len(rounds)
                decided = finished or subject == 'Violoncello'
                intake = {'program': study.program, 'deadline': video_deadline, 'capacity': 2 if master else 4}  # the course one applies to
                video = Course.objects.create(
                    term=term, kind=Course.Kind.AUDITION, title=f'Videostufe {subject}{level}', subject=study.subject, video=True, pass_mark=Decimal('16.1'),
                    released=at(video_deadline + timedelta(days=30) if finished else date(2026, 10, 20), hour=12) if decided else None, **intake,
                ) if subject in VIDEO_SUBJECTS else None
                hall = Course.objects.create(
                    term=term, kind=Course.Kind.AUDITION, title=f'Eignungsprüfung Hauptfach {subject}{level}', subject=study.subject,
                    pass_mark=Decimal('19.1' if master else '16.1'), released=closed, **({} if video else intake),
                )
                panel = self.panel(subject)
                hall.staff.set(panel)
                if video:
                    video.staff.set(panel)
                hall.plan = (at(audition_day + timedelta(days=n // 2), hour=9 if n % 2 == 0 else 14), self.rooms['Festsaal' if n % 2 == 0 else 'Saal Am Palais'], 25)
                rounds[key] = video, hall, panel
            video, hall, panel = rounds[key]
            if outcome == 'running':
                talent = 21.4 if study.person == applicant else rng.uniform(13, 23)
            elif outcome == Study.Status.REJECTED:
                talent = rng.uniform(12, 15.6)
            else:
                talent = rng.uniform(20.5 if master else 18.5, 23.5)
            note = self.repertoire(subject)
            handed = [Document.Kind.CV, Document.Kind.SCHOOL] + [Document.Kind.DEGREE] * master
            if finished or study.since.day < 15 or study.person == applicant:  # the latest applicants still owe the fee receipt
                handed.append(Document.Kind.FEE)
            if study.person.nationality != 'deutsch' and rng.random() < 0.5:
                handed.append(Document.Kind.LANGUAGE)
            if video:
                documents.append(Document(person=study.person, study=study, kind=Document.Kind.VIDEO, title=DEMO_VIDEO[0], url=DEMO_VIDEO[1], created=at(study.since, hour=11)))
            documents += [
                Document(person=study.person, study=study, kind=kind, title=kind.label, url=f'https://upload.hfm-weimar.de/{uuid.uuid4().hex[:10]}.pdf', created=at(study.since, hour=11))
                for kind in handed
            ]
            if not video or (sit(study, video, talent + 1.5, panel, note).passed and video.released):  # videos are judged more kindly
                sit(study, hall, talent, panel, note)
                if finished and not master:
                    for course in shared:
                        sit(study, course, rng.uniform(13, 21), list(course.staff.all()), '')
            if rng.random() < 0.3 or study.person == applicant:
                rows.append(Participation(course=accompanist, study=study, created=at(study.since, hour=12)))
        Participation.objects.bulk_create(rows)
        Score.objects.bulk_create(Score(participation=p, examiner=e, value=v) for p, e, v in scores)
        Document.objects.bulk_create(documents)
        for course in [hall for _, hall, _ in rounds.values()] + shared:
            if count := sum(1 for r in rows if r.course is course):
                begin, room, step = course.plan
                end = begin + timedelta(minutes=step * count or 120)
                if not Booking(room=room, start=begin, end=end).clashes().exists():
                    Booking.objects.create(room=room, start=begin, end=end, person=self.staff['asa'], course=course, category=Booking.Category.AUDITION)
        if not finished:
            failed = Study.objects.filter(start_term=term, participations__course__released__isnull=False, participations__passed=False)
            Study.objects.filter(pk__in=failed.values('pk')).update(status=Study.Status.REJECTED, since=date(2026, 10, 21))

    # Practice rooms and the Postfach

    def practice(self):
        """A week of Übebuchungen under the HfM's rules: in the rooms of one's Institut where no lesson is, booked at most a day
        ahead, four hours a day, more only last-minute; and chamber music rehearsals with the ensemble entered."""
        rng, now, today = self.rng, timezone.localtime(), timezone.localdate()
        taken = defaultdict(list)
        for room, start, end in Booking.objects.filter(start__date__range=(today - timedelta(days=6), today + timedelta(days=1))).values_list('room', 'start', 'end'):
            taken[room].append((start, end))

        def free(room, start, end):
            if all(end <= s or e <= start for s, e in taken[room.pk]):
                taken[room.pk].append((start, end))
                return True
            return False

        usable = [Room.Function.PRACTICE, Room.Function.TEACHING, Room.Function.ORGAN]
        rooms = defaultdict(list)
        for room in Room.objects.filter(function__in=usable).prefetch_related('institutes'):
            for institute in room.institutes.all():
                rooms[institute.pk].append(room)
        practising = [s for s in self.enrolled if s.person != self.demo and rooms[s.subject.institute_id]]
        bookings, used = [], defaultdict(float)
        demo = self.rooms['FH 310']
        start = at(today, 16 * 60)
        if free(demo, start, start + timedelta(minutes=90)):
            bookings.append(Booking(room=demo, start=start, end=start + timedelta(minutes=90), person=self.demo, category=Booking.Category.PRACTICE, created=at(today, 9 * 60 + 12)))
        for offset in range(-6, 2):
            day = today + timedelta(days=offset)
            for study in rng.sample(practising, len(practising) // 2):
                minute = rng.choice(range(8 * 60, 20 * 60, 30))
                minutes = rng.choice([60, 90, 120])
                begin, end = at(day, minute), at(day, minute + minutes)
                last = rng.random() < 0.15  # booked within three hours: does not count
                booked = begin - timedelta(minutes=rng.randint(20, 170) if last else rng.randint(4 * 60, 23 * 60))
                if booked > now or (not last and used[(study.person_id, day)] + minutes / 60 > 4):
                    continue
                room = rng.choice(rooms[study.subject.institute_id])
                if free(room, begin, end):
                    bookings.append(Booking(room=room, start=begin, end=end, person=study.person, category=Booking.Category.PRACTICE, created=booked))
                    used[(study.person_id, day)] += 0 if last else minutes / 60
        Booking.objects.bulk_create(bookings)
        ensembles = [s for s in self.enrolled if s.subject.institute_id in {self.institutes['SH'].pk, self.institutes['KL'].pk} and s.person != self.demo]
        mei = next(s for s in self.enrolled if s.person == self.demo)
        for n, name in enumerate(['FH 110', 'FH 212', 'FH 311', 'FH 324', 'FH 212', 'FH 110']):
            day = today + timedelta(days=1 + n)
            begin = at(day, 18 * 60)
            room = self.rooms[name]
            if not free(room, begin, begin + timedelta(hours=2)):
                continue
            members = rng.sample(ensembles, 3) if n else [mei, *rng.sample(ensembles, 2)]
            rehearsal = Booking.objects.create(
                room=room, start=begin, end=begin + timedelta(hours=2), person=members[0].person, category=Booking.Category.CHAMBER,
                title='Kammermusikprobe Klaviertrio' if n % 2 else 'Kammermusikprobe Streichquartett', created=at(today - timedelta(days=rng.randint(0, 3)), hour=10),
            )
            rehearsal.participants.set(m.person for m in members[1:])

    def events(self):
        """What is on in the coming weeks, and what it takes behind the scenes."""
        C, today, vb, t = Booking.Category, timezone.localdate(), self.staff['veranstaltungsbuero'], self.teachers
        plan = [  # days from today, room, start, minutes, category, title, booked for
            (1, 'Festsaal', '08:00', 90, C.TUNING, 'Stimmung Steinway D', vb),
            (2, 'FH 108', '19:30', 120, C.CLASS_CONCERT, 'Klassenabend Violine · Klasse Prof. Wendt', t['wendt']),
            (3, 'Jenny-Fleischer-Alt-Saal', '19:30', 120, C.CONCERT, 'Liederabend der Gesangsklassen', t['lindner']),
            (4, 'Herz Jesu', '18:00', 60, C.CONCERT, 'Orgelvesper', t['sander']),
            (5, 'HzH 304', '20:00', 150, C.CONCERT, 'Jazz-Session', t['weber']),
            (6, 'FH 108', '10:00', 360, C.MASTERCLASS, 'Meisterkurs Violoncello', t['reinholdt']),
            (7, 'HzH 313', '19:00', 90, C.LECTURE, 'Vortrag: Liszt und die Neudeutsche Schule', t['voss']),
            (8, 'Studiotheater', '19:00', 150, C.CANCELLED, 'Opernszenen', t['falk']),
            (9, 'Festsaal', '19:30', 120, C.CONCERT, 'Kammermusikabend', t['reinholdt']),
            (10, 'Saal Am Palais', '08:00', 480, C.BLOCKED, 'Wartung der Jehmlich-Orgel', vb),
            (12, 'Saal Am Palais', '15:00', 180, C.OPTION, 'Chorkonzert des Hochschulchors', t['hagen']),
            (16, 'Weimarhalle großer Saal', '09:00', 180, C.TRANSPORT, 'Instrumente Festsaal → Weimarhalle', vb),
            (16, 'Weimarhalle großer Saal', '14:00', 180, C.ORCHESTRA, 'Generalprobe Hochschulorchester', t['ferreira']),
            (16, 'Weimarhalle großer Saal', '19:30', 150, C.CONCERT, 'Sinfoniekonzert des Hochschulorchesters', t['ferreira']),
            (16, 'Weimarhalle großer Saal', '18:30', 240, C.CONCERT_SUPPORT, 'Abenddienst Sinfoniekonzert', vb),
            (21, 'Festsaal', '10:00', 360, C.RENTAL, 'Vermietung: Festakt der Stadt Weimar', vb),
            (23, 'Stadtschloss neuer Veranstaltungssaal', '17:00', 90, C.CONCERT, 'Musik im Schloss', t['brueck']),
        ]
        for offset, name, begin, minutes, category, title, person in plan:
            for day in (today + timedelta(days=offset + k) for k in range(4)):  # the first free day
                booking = Booking(room=self.rooms[name], start=at(day, clock(begin)), end=at(day, clock(begin) + minutes), person=person, category=category, title=title)
                if category == C.CONCERT_SUPPORT or not booking.clashes().exists():
                    booking.save()
                    break
            if category == C.CLASS_CONCERT:
                booking.participants.set(s.person for s in Study.objects.filter(teacher=person, status=Study.Status.ENROLLED))

    def amend(self, by, when, obj, **values):
        """Change a record as `by` did on `when`, so the history has something to show."""
        obj = type(obj).objects.get(pk=obj.pk)
        for name, value in values.items():
            setattr(obj, name, value)
        with history.acting(by):
            obj.save()
        Change.objects.filter(pk=Change.objects.latest('pk').pk).update(at=when)

    def changes(self):
        asa, office, events = self.staff['asa'], self.staff['pruefungsamt'], self.staff['veranstaltungsbuero']
        self.amend(asa, at(date(2026, 9, 28), hour=10), self.demo, address='Jakobstraße 7\n99423 Weimar')
        corrected = {Decimal('2.3'): Decimal('2.0'), Decimal('2.0'): Decimal('1.7'), Decimal('1.7'): Decimal('1.3')}
        exam = Participation.objects.filter(study__person=self.demo, course__kind=Course.Kind.EXAM, grade__in=list(corrected)).exclude(course__released=None).select_related('course').first()
        self.amend(office, exam.course.released + timedelta(days=9), exam, grade=corrected[exam.grade])  # after the Einsichtnahme
        score = Score.objects.filter(participation__study__person=self.applicant, participation__course__video=True).select_related('examiner').first()
        self.amend(score.examiner, at(date(2026, 10, 18), hour=21), score, value=min(score.value + 1, 25))
        concert = Booking.objects.filter(category=Booking.Category.CONCERT, start__gte=timezone.now()).first()
        self.amend(events, timezone.now() - timedelta(days=2), concert, end=concert.end + timedelta(minutes=30))

    def letters(self):
        demo, applicant, wendt = self.demo, self.applicant, self.teachers['wendt']
        Message.objects.bulk_create([
            Message(person=demo, subject='Prüfungsanmeldung vom 01. bis 15.11.2026', created=at(date(2026, 10, 15), hour=9),
                    body='Melden Sie sich bitte im Campus zu den Prüfungen dieses Semesters an. Abmeldung ist bis zum 18.01.2027 möglich.',
                    link='/pruefungen/'),
            Message(person=demo, subject='Ergebnis: Künstlerische Präsentation I · Künstlerisch-praktische Prüfung (20 Min.) · Violine', created=at(date(2026, 7, 30), hour=12),
                    body='Ihr Prüfungsergebnis ist freigegeben.', link='/pruefungen/', read=at(date(2026, 7, 30), hour=18)),
            Message(person=applicant, subject='Bewerbung eingegangen', created=at(date(2026, 10, 3), hour=11), read=at(date(2026, 10, 3), hour=12),
                    body='Ihre Bewerbung für B.Mus. Streichinstrumente und Harfe, Hauptfach Violoncello, ist eingegangen.', link='/aufnahme/meine/'),
            Message(person=applicant, subject='Einladung zur nächsten Prüfungsstufe', created=at(date(2026, 10, 20), hour=14),
                    body='Sie haben die Videostufe bestanden und sind zur Eignungsprüfung Hauptfach Violoncello eingeladen. Ihren Termin finden Sie unter „Meine Bewerbung“.',
                    link='/aufnahme/meine/'),
            Message(person=wendt, subject='Videostufe Violine: Ihre Wertungen fehlen', created=at(date(2026, 10, 19), hour=8),
                    body='Für einige Bewerbungen der Videostufe Violine fehlen noch Ihre Wertungen. Die Bewerbungsfrist endet am 02.11.2026.',
                    link='/aufnahme/kommissionen/'),
        ])
        notices = []  # the Bescheide already issued: decisions on applications, and the enrolment of this term's newcomers
        for study in Study.objects.filter(status__in=list(DECISIONS)).select_related('person', 'program', 'subject', 'start_term'):
            title, text = DECISIONS[study.status]
            text = text.format(
                program=f'{study.program.degree} {study.program.name}', subject=study.subject, term=study.start_term,
                deadline=f'{study.since + timedelta(days=ACCEPT_DAYS):%d.%m.%Y}',
            )
            notices.append((study, title, f'{text}\n\n{APPEAL}' if study.status == Study.Status.REJECTED else text, at(study.since, hour=10)))
        for study in (s for s in self.enrolled if s.fs == 1):
            notices.append((study, 'Immatrikulationsbescheid', (
                f'Willkommen an der Hochschule für Musik FRANZ LISZT Weimar. Sie sind ab dem {study.start_term} im Studiengang '
                f'{study.program.degree} {study.program.name}, Hauptfach {study.subject}, immatrikuliert.\n\nIhre Matrikelnummer: {study.person.matriculation}'
            ), at(study.start_term.start - timedelta(days=10), hour=10)))
        sent = Message.objects.bulk_create(Message(person=s.person, subject=title, body=text, created=when, read=when) for s, title, text, when in notices)
        Document.objects.bulk_create(Document(person=s.person, study=s, kind=Document.Kind.NOTICE, title=title, message=m, created=when) for (s, title, _, when), m in zip(notices, sent))
