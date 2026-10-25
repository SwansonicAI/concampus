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
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from admissions.retention import forget, months_before
from admissions.views import DECISIONS
from core import destatis, history, texts
from core.models import Change, Holiday, Institute, Institution, Message, Office, Person, Term, Text
from courses.models import Area, Course, Ensemble, Format, Participation, Score, Signup
from courses.panel import mean
from curriculum.models import Module, Program, Subject
from instruments.models import Instrument, Loan
from rooms.models import Booking, BookingCategory, Room, RoomFunction
from students.models import Document, DocumentType, Study, StudyTerm
from ..plans import HF, JENA, PIANO, PROGRAMS, RESEARCH, SPEECH, STRINGS, TEACHING, WINDS, WS

FEE = Decimal('341.30')  # Semesterbeitrag WiSe 2026/27: Studierendenwerk 95,00 + Deutschlandsemesterticket 226,80 + Studierendenrat 19,50
FEES = {  # published Semesterbeiträge from WiSe 2023/24; earlier ones approximated, SoSe 2027 as WiSe 2026/27
    'SoSe 2022': Decimal('205.30'), 'WiSe 2022/23': Decimal('211.30'), 'SoSe 2023': Decimal('219.80'), 'WiSe 2023/24': Decimal('230.30'),
    'SoSe 2024': Decimal('223.80'), 'WiSe 2024/25': Decimal('279.40'), 'SoSe 2025': Decimal('279.40'), 'WiSe 2025/26': Decimal('313.30'),
    'SoSe 2026': Decimal('323.30'),
}
FEMALE = {
    'Anna', 'Clara', 'Lea', 'Nora', 'Hannah', 'Ida', 'Greta', 'Svea', 'Maja', 'Frieda', 'Marlene', 'Mei Lin', 'Xinyi', 'Jiayi', 'Seo-yeon', 'Ha-eun',
    'Aiko', 'Haruka', 'Yuki', 'Zofia', 'Marta', 'Aleksandra', 'Chiara', 'Valentina', 'Giulia', 'Sofiia', 'Anastasiia', 'Kateryna',
    'Mia', 'Emma', 'Sophie', 'Lina', 'Johanna', 'Charlotte', 'Helena', 'Mathilda', 'Paula', 'Luise', 'Ruoxi', 'Shuang', 'Qing', 'Ji-woo', 'Soo-ah', 'Mio',
    'Julia', 'Katarzyna', 'Francesca', 'Sofia', 'Iryna', 'Yuliia', 'Lena', 'Magdalena', 'Theresa', 'Camille', 'Léa', 'Manon', 'Lucía', 'Carmen', 'Elena',
    'Emily', 'Sarah', 'Olivia',
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
KREISE = {  # the Kreise of the German places students come from (core.destatis)
    'Weimar': '16055', 'Erfurt': '16051', 'Jena': '16053', 'Leipzig': '14713', 'Dresden': '14612', 'Berlin': '11000', 'Hamburg': '02000',
    'München': '09162', 'Köln': '05315', 'Halle (Saale)': '15002', 'Gotha': '16067', 'Kassel': '06611', 'Nürnberg': '09564',
}
OFFICIAL = {  # Abschlussprüfung, Studienart, Studienfach and Prüfungsamt of each degree, as the official statistics code them
    'B.Mus.': ('83', '1', '', ''), 'M.Mus.': ('89', '7', '', ''), 'B.Ed.': ('38', '1', '113', ''), 'M.Ed.': ('64', '7', '113', ''),
    'Staatsexamen': ('25', '1', '113', '5091601'), 'Diplom': ('73', '1', '193', ''), 'Konzertdiplom': ('71', '3', '', ''), 'Zertifikat': ('80', '3', '', ''),
    'Dr. phil.': ('06', '5', '', ''), 'Gasthörerschaft': ('97', '9', '', ''),
}
SUBJECT_FIELDS = {  # Studienfach of each Hauptfach
    **dict.fromkeys(STRINGS + WINDS, '165'), 'Gesang': '230', 'Jazz-Gesang': '164', 'Klavier': '080', 'Orgel': '080', 'Musikpädagogik': '113',
    'Musikwissenschaft': '114', 'Kulturmanagement': '040',
}
EARLIER = ['2740', '2800', '2520', '2500', '2720', '2620', '0580']  # where postgraduates from Germany took their first degree
HOLIDAYS = [  # Thuringia's public holidays in this year's terms and the Christmas break: name, first day, last day
    ('Tag der Deutschen Einheit', date(2026, 10, 3), None), ('Reformationstag', date(2026, 10, 31), None),
    ('Weihnachtspause', date(2026, 12, 21), date(2027, 1, 3)), ('Karfreitag', date(2027, 3, 26), None), ('Ostermontag', date(2027, 3, 29), None),
    ('Tag der Arbeit', date(2027, 5, 1), None), ('Christi Himmelfahrt', date(2027, 5, 6), None), ('Pfingstmontag', date(2027, 5, 17), None),
    ('Weltkindertag', date(2027, 6, 1), None),
]
EXAM_DAYS = [d for d in (date(2027, 2, 8) + timedelta(days=n) for n in range(19)) if d.weekday() < 5]  # Prüfungszeitraum 08.–26.02.2027
LECTURES = {  # published lecture periods; other terms are approximated
    'WiSe 2025/26': (date(2025, 10, 8), date(2026, 2, 10)), 'SoSe 2026': (date(2026, 4, 8), date(2026, 7, 14)),
    'WiSe 2026/27': (date(2026, 10, 5), date(2027, 2, 5)), 'SoSe 2027': (date(2027, 4, 7), date(2027, 7, 13)),
}
ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII']
INSTITUTES = {  # the HfM's ten Institute
    'AM': 'Alte Musik', 'BS': 'Blasinstrumente und Schlagwerk', 'DO': 'Dirigieren und Opernkorrepetition', 'GM': 'Gesang und Musiktheater',
    'GI': 'Gitarre', 'KL': 'Klavier', 'MK': 'Musikpädagogik und Kirchenmusik', 'MW': 'Musikwissenschaft, Kulturmanagement, Musiktheorie',
    'NJ': 'Neue Musik und Jazz', 'SH': 'Streichinstrumente und Harfe',
}
SUBJECT_INSTITUTE = {
    **dict.fromkeys(STRINGS, 'SH'), **dict.fromkeys(WINDS, 'BS'), 'Gesang': 'GM', 'Jazz-Gesang': 'NJ', 'Klavier': 'KL', 'Orgel': 'MK',
    'Musikpädagogik': 'MK', 'Musikwissenschaft': 'MW', 'Kulturmanagement': 'MW',
}
SUBJECTS_EN = {
    'Violine': 'Violin', 'Viola': 'Viola', 'Violoncello': 'Violoncello', 'Kontrabass': 'Double Bass', 'Harfe': 'Harp', 'Flöte': 'Flute', 'Oboe': 'Oboe',
    'Klarinette': 'Clarinet', 'Fagott': 'Bassoon', 'Horn': 'Horn', 'Trompete': 'Trumpet', 'Posaune': 'Trombone', 'Tuba': 'Tuba', 'Schlagwerk': 'Percussion',
    'Gesang': 'Voice', 'Jazz-Gesang': 'Jazz Voice', 'Klavier': 'Piano', 'Orgel': 'Organ', 'Musikwissenschaft': 'Musicology',
    'Musikpädagogik': 'Music Education', 'Kulturmanagement': 'Arts Management',
}
PERFORMANCE = (
    'Graduates perform as soloists, chamber musicians and orchestral musicians at a professional level. They command a repertoire across the '
    'periods, apply historical and contemporary performance practice, and reflect on their work in the light of musicology and music theory.'
)
SCHOOL = 'Music for Gymnasien (double subject)'
SUPPLEMENT = {  # for the Diploma Supplement: qualification, field of study in English, level, learning outcomes
    ('B.Mus.', 'Streichinstrumente und Harfe'): ('Bachelor of Music (B.Mus.)', 'Strings and Harp', 'bachelor', PERFORMANCE),
    ('B.Mus.', 'Blasinstrumente und Schlagwerk'): ('Bachelor of Music (B.Mus.)', 'Wind Instruments and Percussion', 'bachelor', PERFORMANCE),
    ('B.Mus.', 'Gesang | Musiktheater'): ('Bachelor of Music (B.Mus.)', 'Voice | Music Theatre', 'bachelor', PERFORMANCE),
    ('B.Mus.', 'Klavier'): ('Bachelor of Music (B.Mus.)', 'Piano', 'bachelor', PERFORMANCE),
    ('M.Mus.', 'Streichinstrumente'): (
        'Master of Music (M.Mus.)', 'Strings', 'master',
        'Graduates have an individual artistic profile and perform demanding repertoire at an advanced professional level as soloists, in '
        'chamber music and in orchestras. They prepare programmes independently and reflect critically on interpretation.',
    ),
    ('B.Ed.', 'Musik an Gymnasien (Doppelfach)'): (
        'Bachelor of Education (B.Ed.)', SCHOOL, 'bachelor',
        'Graduates combine artistic skills on their instrument or voice with music pedagogy, music theory and musicology. They plan, give and '
        'reflect on music lessons at secondary level and lead school ensembles.',
    ),
    ('M.Ed.', 'Musik an Gymnasien (Doppelfach)'): (
        'Master of Education (M.Ed.)', SCHOOL, 'master',
        'Graduates teach music at Gymnasien: they plan, give and evaluate lessons, lead choirs and ensembles, and ground their teaching in music '
        'pedagogy and research. The degree qualifies for the preparatory service (Vorbereitungsdienst) for teachers.',
    ),
    ('Diplom', 'Kirchenmusik A'): (
        'Diplom-Kirchenmusiker/in (A-Examen)', 'Church Music (A)', 'diplom',
        'Graduates master liturgical and concert organ playing, improvisation, choral and orchestral conducting and the liturgical repertoire, '
        'as church musicians at the highest level.',
    ),
}
THESIS_WEEKS = {'B.Mus.': 8, 'B.Ed.': 12, 'M.Ed.': 20, 'Staatsexamen': 16, 'Diplom': 12}
THESES = ('Wissenschaftliche Arbeit', 'Wissenschaftliche Hausarbeit', 'Bachelorarbeit', 'Masterarbeit', 'Diplomarbeit')  # exams written as theses
TOPICS = [
    'Historische Aufführungspraxis im Repertoire für {i}: Quellen und Folgen für heute', 'Werke für {i} im Weimarer Umfeld Franz Liszts',
    'Auftrittsangst und Vorbereitung im Studium des Fachs {i}', 'Zeitgenössische Spieltechniken für {i} seit 1960',
    'Unterricht im Fach {i} an Thüringer Musikschulen: eine Befragung', 'Bearbeitungen für {i}: Werktreue und Klangideal',
]
CLASSES = 'weitere Instrumental-, Vokal- und Dirigierklassen'
AREAS = [  # the Fachgebiete under which the HfM's Moodle lists each semester's courses
    'Dirigieren', 'Elementare Musikpädagogik', 'Gesang / Musiktheater', 'Gitarre', 'Hochschulensembles', 'Instrumental- und Gesangspädagogik',
    'Jazz', 'Kammermusik', 'Kirchenmusik', 'Komposition / Elektroakustische Komposition', 'Kulturmanagement', 'Musikpädagogik', 'Musiktheorie',
    'Musikwissenschaft', 'Schlüsselqualifikationen / Berufsorientierung', CLASSES,
]
AREA_OF = {  # course title (or its start) → Fachgebiet
    **dict.fromkeys(['Allgemeine Musiklehre', 'Formenlehre', 'Gehörbildung', 'Historische Satzlehre', 'Höranalyse', 'Kontrapunkt', 'Satztechniken',
                     'Werkanalyse', 'Instrumentenkunde'], 'Musiktheorie'),
    **dict.fromkeys(['Musikgeschichte', 'Geschichte des Klavierliedes', 'Soundtrack', '„Pour la fin', 'Brasilianische', 'Einführung in das wissenschaftliche',
                     'Schreibwerkstatt', 'Doktorandenkolloquium'], 'Musikwissenschaft'),
    **dict.fromkeys(['Fachdidaktik Musik', 'Musikpädagogisches', 'Begleitseminar', 'Schulpraktisches', 'Unterrichtspraktisches', 'Chorleitung',
                     'Grundlagen Bandleitung', 'Improvisation (jazz', 'Rhetorik', 'Stimmbildung', 'Instrumentenpool', 'Bachelorprojekt', 'Masterprojekt',
                     'Musik und Gesundheit', 'Stimmphysiologie', 'Anatomie', 'Alexander-Technik', 'Mentales Training', 'Auftrittstraining',
                     'Konditionstraining'], 'Musikpädagogik'),
    **dict.fromkeys(['Fachdidaktik', 'Lehrpraxis', 'Musikpädagogik 1', 'Musikpädagogik 3'], 'Instrumental- und Gesangspädagogik'),
    **dict.fromkeys(['Elementare Musikpädagogik', 'Rhythmik'], 'Elementare Musikpädagogik'),
    **dict.fromkeys(['Kammermusik', 'Historische Spielpraxis', 'Instrumentale Improvisation'], 'Kammermusik'),
    **dict.fromkeys(['Hochschulchor', 'Hochschulorchester', 'Kammerorchester', 'Liszt Brass', 'Ensemble für Alte Musik'], 'Hochschulensembles'),
    **dict.fromkeys(['Hymnologie', 'Liturg', 'Historische Tasteninstrumente'], 'Kirchenmusik'),
    **dict.fromkeys(['Aufführungspraxis des Rezitativs', 'Bühnensprechen', 'Grundlagen Bühnensprechen', 'Grundlagen Phonetik', 'Grundlagen Schauspiel',
                     'Italienisch', 'Körpererfahrung', 'Künstlerische Liedgestaltung', 'Liedgestaltung', 'Opernpartien', 'Phonetik', 'Proben und Aufführungen',
                     'Repertoirekunde Lied', 'Szenische', 'Konzert- und Theaterpraxis', 'Ensemblesingen'], 'Gesang / Musiktheater'),
    **dict.fromkeys(['Grundlagen Dirigieren', 'Orchesterleitung', 'Einführung in die Korrepetition', 'Klavierauszugspiel', 'Korrepetition'], 'Dirigieren'),
    **dict.fromkeys(['Tonstudiotechnik', 'Musikalische Wirkungsforschung'], 'Komposition / Elektroakustische Komposition'),
    **dict.fromkeys(['Einführung Berufspraxis', 'Einführung in die Musikvermittlung', 'Selbstmanagement'], 'Schlüsselqualifikationen / Berufsorientierung'),
    **dict.fromkeys(['Improvisation', 'Hauptfach Jazz'], 'Jazz'),
    **dict.fromkeys(['Hauptfach', 'Gesang ·', 'Klavier (Pflichtfach)', 'Klavier im', 'Werkstudium', 'Zweitinstrument', 'Historisches Instrument',
                     'Literaturkunde', 'Orchesterstudien', 'Künstlerische Projektarbeit'], CLASSES),
}
FORMATS = [  # the Lehrformen and their abbreviations in the Studienpläne
    ('Vorlesung', 'V'), ('Seminar', 'S'), ('Übung', 'Ü'), ('Gruppenunterricht', 'G'), ('Kleingruppenunterricht', 'E+x'), ('Einzelunterricht', 'E'),
    ('Projekt', 'P'), ('Kolloquium', 'K'),
]
ENSEMBLES = [  # Kammermusik's usual groupings: name, parts, coaching family, repertoire
    ('Streichquartett', ['Violine', 'Violine', 'Viola', 'Violoncello'], 'strings', 'Haydn: Streichquartett op. 76 Nr. 4 „Sonnenaufgang“\nSchostakowitsch: Streichquartett Nr. 8 c-Moll op. 110'),
    ('Klaviertrio', ['Klavier', 'Violine', 'Violoncello'], 'piano', 'Beethoven: Klaviertrio B-Dur op. 97 „Erzherzog“'),
    ('Bläserquintett', ['Flöte', 'Oboe', 'Klarinette', 'Fagott', 'Horn'], 'winds', 'Reicha: Bläserquintett op. 88 Nr. 2\nLigeti: Sechs Bagatellen'),
    ('Blechbläserquintett', ['Trompete', 'Trompete', 'Horn', 'Posaune', 'Tuba'], 'winds', 'Ewald: Blechbläserquintett Nr. 1 b-Moll op. 5'),
    ('Schlagzeugensemble', ['Schlagwerk', 'Schlagwerk', 'Schlagwerk'], 'winds', 'Reich: Drumming, Teil 1'),
    ('Klavierquintett', ['Klavier', 'Violine', 'Violine', 'Viola', 'Violoncello'], 'piano', 'Schumann: Klavierquintett Es-Dur op. 44'),
    ('Duo Violine/Klavier', ['Violine', 'Klavier'], 'piano', 'Brahms: Sonate G-Dur op. 78'),
    ('Duo Violoncello/Klavier', ['Violoncello', 'Klavier'], 'piano', 'Debussy: Sonate d-Moll'),
    ('Duo Viola/Klavier', ['Viola', 'Klavier'], 'piano', 'Schumann: Märchenbilder op. 113'),
    ('Duo Kontrabass/Klavier', ['Kontrabass', 'Klavier'], 'piano', 'Bottesini: Elegia Nr. 1 D-Dur'),
    ('Duo Flöte/Klavier', ['Flöte', 'Klavier'], 'piano', 'Poulenc: Sonate für Flöte und Klavier'),
    ('Duo Flöte/Harfe', ['Flöte', 'Harfe'], 'winds', 'Ibert: Entracte'),
    ('Liedduo', ['Gesang', 'Klavier'], 'voice', 'Schubert: Winterreise D 911, Auswahl'),
    ('Violinduo', ['Violine', 'Violine'], 'strings', 'Bartók: 44 Duos für zwei Violinen, Auswahl'),
    ('Klavierduo', ['Klavier', 'Klavier'], 'piano', 'Schubert: Fantasie f-Moll D 940'),
    ('Horntrio', ['Horn', 'Violine', 'Klavier'], 'piano', 'Brahms: Horntrio Es-Dur op. 40'),
    ('Trompetenensemble', ['Trompete', 'Trompete', 'Trompete'], 'winds', 'Gabrieli: Canzonen, Auswahl'),
]
INSTRUMENTS = {  # the Institute's instruments for lending: name, maker, condition and accessories, lendable
    'SH': [
        ('Violine 4/4', 'Mittenwald, um 1920', 'Mit Etui und Bogen', True), ('Violine 4/4', 'Markneukirchen, 1958', 'Mit Etui und Bogen', True),
        ('Viola 41 cm', 'Werkstatt Weimar, 1987', 'Mit Etui und Bogen', True), ('Viola 40,5 cm', 'Erlbach, 1972', 'Mit Etui, ohne Bogen', True),
        ('Violoncello 4/4', 'Mittenwald, 1931', 'Mit Hülle und Bogen', True), ('Violoncello 4/4', 'Markneukirchen, 1965', 'Mit Hülle und Bogen', True),
        ('Kontrabass 3/4', 'Böhmen, um 1900', 'Deutscher Bogen, Hülle', True), ('Kontrabass 3/4', 'Markneukirchen, 1979', 'In Reparatur: Riss in der Decke', False),
        ('Konzertharfe', 'Lyon & Healy, Style 30', 'Nur im Haus', True),
    ],
    'BS': [
        ('Fagott', 'Heckel, 1964', '', True), ('Kontrafagott', 'Mollenhauer, 2003', '', True), ('Oboe d’amore', 'Marigaux', '', True),
        ('Englischhorn', 'Lorée', '', True), ('Bassklarinette', 'Buffet Crampon', 'Bis tief C', True), ('Wagnertuba in B', 'Alexander', '', True),
        ('Piccolotrompete', 'Schilke', '', True), ('Marimbaphon 5 Oktaven', 'Adams', 'Fahrbar, nur im Haus', True),
    ],
    'AM': [('Barockvioline', 'Kopie nach Stainer', 'Darmsaiten, Barockbogen', True), ('Viola da gamba (Bass)', 'Kopie nach Colichon', 'Mit Bogen', True), ('Barockoboe', 'Kopie nach Denner', '', True)],
    'MK': [
        ('Konzertgitarre', 'Höfner', 'Mit Tasche', True), ('Konzertgitarre', 'Höfner', 'Mit Tasche', True), ('Ukulele (Sopran)', 'Kala', '', True),
        ('Ukulele (Sopran)', 'Kala', '', True), ('Cajón', 'Schlagwerk', '', True), ('E-Bass', 'Fender Jazz Bass', 'Mit Kabel, ohne Verstärker', True),
        ('Keyboard 61 Tasten', 'Yamaha', 'Mit Netzteil', True), ('Glockenspiel (Sopran)', 'Sonor', '', True),
    ],
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
        return 'orgel' if name == 'Herz Jesu' else 'veranstaltung', *VENUES[name]
    p = purpose.lower()
    if 'sitzung' in p or 'senat' in p:
        return 'sitzung', *VENUES[name]
    if 'hörsaal' in p:
        return 'seminar', *VENUES[name]
    if 'seminar' in p or 'konferenz' in p:
        return 'seminar', 24, 'Klavier, Beamer'
    if 'werkstatt' in p:
        return 'werkstatt', 2, 'Werkbank'
    if 'studio' in p or 'hörlabor' in p:
        return 'studio', 8, 'Aufnahmetechnik' if 'studio' in p else 'Kopfhörerplätze'
    if name in ORGAN_ROOMS:
        return 'orgel', 8, 'Orgel'
    if name in CHAMBER_ROOMS or 'ensemble' in p or 'projekt' in p or 'band' in p:
        return 'kammermusik', 16, 'Flügel, Notenpulte' if 'jazz' not in p and 'band' not in p else 'Flügel, Schlagzeug, Verstärker'
    equipment = ('Marimba, Pauken' if 'schlagwerk' in p else 'Flügel, Spiegel' if 'gesang' in p or 'lied' in p or 'oper' in p
                 else 'Zwei Flügel' if 'dirig' in p or 'klavier' in p else 'Cembalo' if 'alte musik' in p else 'Flügel, Verstärker' if 'jazz' in p else 'Klavier')
    if 'übungsraum' in p or 'überaum' in p:
        return 'uebung', 3, equipment
    return 'unterricht', 6, equipment


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
POSTGRADUATE = ['M.Mus.', 'M.Ed.', 'Konzertdiplom', 'Zertifikat', 'Dr. phil.']
VIDEO_NOTE = (
    'Eine Datei, ungeschnitten, 10–15 Minuten, beginnend mit einer kurzen Vorstellung; die Kamera 3–5 m entfernt, '
    'eine professionelle Aufnahme ist nicht nötig. Nicht älter als ein Jahr. Höchstens 1,5 GB als .mp4, .mov, .webm, .m4v, .ogg, .ogv oder .qt.'
)
DOCUMENTS = [  # key, name, condition, settings: what a complete application holds, as the HfM lists it
    ('video', 'Video', 'video', {'note': VIDEO_NOTE, 'file_types': '.mp4, .mov, .webm, .m4v, .ogg, .ogv, .qt', 'max_mb': 1536, 'single': True}),
    ('cv', 'Lebenslauf', 'always', {}),
    ('school', 'Abitur- oder letztes Schulzeugnis', 'always', {}),
    ('degree', 'Bachelor- oder Masterzeugnis, Transcript', 'degree', {}),
    ('fee', 'Einzahlungsbeleg Eignungsprüfungsgebühr', 'always', {'note': '50 €, Verwendungszweck: Eignungsprüfung, Name, Hauptfach.'}),
    ('consent', 'Einwilligung der Erziehungsberechtigten', 'minor', {'note': 'Sie sind noch nicht volljährig.'}),
    ('voice', 'Phoniatrisches Gutachten', 'subjects', {'required': False}),
    ('language', 'Sprachnachweis Deutsch', 'foreign', {'required': False, 'note': 'Niveau B2.'}),
    ('notice', 'Bescheid', 'never', {}),
    ('other', 'Sonstiges', 'never', {}),
]
TEXTS = {  # where the HfM words things its own way (core.texts has the defaults)
    'absender_bescheide': 'Akademische und Studentische Angelegenheiten',
    'absender_bescheinigungen': 'Akademische und Studentische Angelegenheiten',
    'absender_leistungen': 'Prüfungsamt',
    'einladung': 'Ihr Termin: {termin} Uhr, {raum}.\n\nBitte erscheinen Sie 30 Minuten vorher. Einspielräume stehen bereit.',
    'bewerbung_hinweise': (
        'Pro Studiengang und Hauptfach ist eine Bewerbung möglich. Nach dem Absenden laden Sie unter „Meine Bewerbung“ Ihr Video und Ihre '
        'Unterlagen hoch: Lebenslauf, Schulzeugnis, Einzahlungsbeleg der Prüfungsgebühr (50 €), für einen Master auch Ihr Bachelorzeugnis. '
        'Bis zur Frist können Sie alles ändern.\n\n'
        'Wo eine Videostufe vorgesehen ist, ist sie die erste Prüfungsstufe: ein ungeschnittenes Video von 10–15 Minuten in einer Datei, '
        'beginnend mit einer kurzen Vorstellung, nicht älter als ein Jahr, höchstens 1,5 GB. Eine professionelle Aufnahme ist nicht nötig.\n\n'
        'Ihr Ergebnis erhalten Sie spätestens drei Wochen nach der Prüfung im Portal.\n\n'
        'Die Kommission besteht aus mindestens drei Lehrenden des Hauptfachs. Jedes Mitglied wertet mit 0 bis 25 Punkten; bestanden ist '
        'das Hauptfach ab 16,1 Punkten, die übrigen Teile ab 11,6.\n\n'
        'Einladungen und Bescheide erhalten Sie in Ihr Postfach.'
    ),
}
INSURERS = ['Techniker Krankenkasse', 'AOK PLUS', 'BARMER', 'DAK-Gesundheit', 'IKK classic', 'KKH Kaufmännische Krankenkasse', 'hkk Krankenkasse']
FUNCTIONS = [  # key, name, given out centrally: the room functions Asimut lists
    ('uebung', 'Übungsraum', False), ('unterricht', 'Unterrichtsraum', False), ('kammermusik', 'Kammermusik', False), ('orgel', 'Orgel', False),
    ('seminar', 'Hörsaal und Seminarraum', False), ('studio', 'Studio und Labor', False), ('sitzung', 'Sitzungsraum', True),
    ('veranstaltung', 'Veranstaltungsraum', True), ('werkstatt', 'Werkstatt', False),
]
BY_STUDENTS, BY_TEACHERS, COUNTED = {'students': True}, {'teachers': True}, {'quota': True}
PUBLIC = {'public': True}
CATEGORIES = [  # key, name, colour, settings: the HfM's booking categories as its Asimut has them
    ('unterricht', 'Unterricht', 'teach', BY_TEACHERS), ('uebung', 'Übebuchung', 'practice', BY_STUDENTS | BY_TEACHERS | COUNTED),
    ('kammermusik', 'Kammermusik-Probe', 'practice', BY_STUDENTS | COUNTED | {'ahead_hours': 240, 'participants': 1}),
    ('probe', 'Probe', 'teach', BY_TEACHERS), ('orchesterprobe', 'Orchesterprobe', 'teach', {}), ('registerprobe', 'Registerprobe', 'teach', BY_TEACHERS),
    ('pruefung', 'Prüfung', 'exam', BY_TEACHERS), ('eignungspruefung', 'Eignungsprüfung', 'exam', {}), ('vorspiel', 'Vorspiel', 'exam', BY_TEACHERS | PUBLIC),
    ('klassenabend', 'Klassenabend', 'event', BY_TEACHERS | PUBLIC), ('konzert', 'Konzert', 'event', PUBLIC), ('veranstaltung', 'Veranstaltung', 'event', PUBLIC),
    ('meisterkurs', 'Meisterkurs', 'event', BY_TEACHERS | PUBLIC), ('weimarer-meisterkurse', 'Weimarer Meisterkurse', 'event', PUBLIC),
    ('wettbewerb', 'Wettbewerb', 'exam', PUBLIC), ('workshop', 'Workshop', 'event', BY_TEACHERS | PUBLIC), ('vortrag', 'Vortrag', 'event', BY_TEACHERS | PUBLIC),
    ('tagung', 'Tagung', 'event', PUBLIC), ('sitzung', 'Sitzung', 'event', BY_TEACHERS), ('vorstellung', 'Vorstellungsgespräche', 'exam', {}),
    ('aufnahme', 'Aufnahme', 'teach', BY_TEACHERS), ('projekt', 'Studentisches Projekt', 'teach', {}), ('vermietung', 'Vermietung', 'event', {}),
    ('option', 'freie Konzert-Option', 'event', {}), ('konzertbetreuung', 'Konzertbetreuung', 'event', {}), ('organisation', 'Organisation', 'event', {}),
    ('stimmung', 'Stimmung', 'service', {}), ('klavierstimmer', 'Reparaturarbeiten Klavierstimmer', 'service', {}), ('hausmeister', 'Hausmeister', 'service', {}),
    ('technik', 'Technik', 'service', {}), ('transport', 'Transport', 'service', {}), ('persoenlich', 'Persönliche Buchung', 'practice', BY_TEACHERS),
    ('allgemein', 'Allgemeine Buchung', 'practice', {}), ('blockiert', 'Blockiert', 'blocked', {}), ('abgesagt', 'Abgesagt', 'cancelled', {'blocks': False}),
]
OFFICES = {  # the HfM's offices and what each may do (core.access.CAPABILITIES)
    'ASA': [  # Akademische und Studentische Angelegenheiten
        'view_students', 'view_files', 'manage_students', 'manage_reregistration', 'message_students',
        'view_applications', 'decide_applications', 'run_auditions', 'manage_courses', 'view_teaching_load',
        'view_curriculum', 'book_any_room', 'view_bookings', 'manage_terms', 'view_statistics', 'report_statistics',
    ],
    'Prüfungsamt': [
        'view_files', 'manage_exams', 'decide_results', 'recognise', 'manage_courses', 'view_teaching_load',
        'view_curriculum', 'book_any_room', 'view_bookings',
    ],
    'Dekanat': ['manage_courses', 'view_teaching_load', 'roll_over', 'view_curriculum', 'edit_curriculum', 'book_any_room', 'view_bookings', 'view_statistics'],
    'Veranstaltungsbüro': ['book_any_room', 'view_bookings', 'manage_bookings', 'manage_rooms', 'manage_instruments'],
    'Campus-Team': ['manage_accounts', 'manage_institution', 'manage_terms'],
    'Präsidium': ['view_statistics', 'view_curriculum', 'view_teaching_load'],
    'Institut Streichinstrumente und Harfe': [
        'view_students', 'view_files', 'message_students', 'view_applications', 'run_auditions', 'manage_courses', 'view_teaching_load',
        'view_curriculum', 'book_any_room', 'view_bookings', 'manage_bookings', 'manage_rooms', 'manage_instruments',
    ],
    'Prüfungsausschuss Lehramt': ['view_files', 'manage_exams', 'decide_results', 'recognise', 'view_curriculum'],
}
LIMITS = {  # offices for some Institute or Studiengänge only
    'Institut Streichinstrumente und Harfe': (['SH'], []),
    'Prüfungsausschuss Lehramt': ([], [('B.Ed.', 'Musik an Gymnasien (Doppelfach)'), ('M.Ed.', 'Musik an Gymnasien (Doppelfach)'), ('Staatsexamen', 'Lehramt an Gymnasien, Studienfach Musik')]),
}
STAFF = [  # username, title, first, last, office
    ('asa', '', 'Sabine', 'Krüger', 'ASA'),
    ('pruefungsamt', '', 'Thomas', 'Albrecht', 'Prüfungsamt'),
    ('dekanat', 'Prof. Dr.', 'Johanna', 'Brandt', 'Dekanat'),
    ('veranstaltungsbuero', '', 'Michael', 'Görlitz', 'Veranstaltungsbüro'),
    ('campus', '', 'Lena', 'Hofmann', 'Campus-Team'),
    ('praesidium', 'Prof. Dr.', 'Andreas', 'Lindemann', 'Präsidium'),
    ('institut', '', 'Katrin', 'Vogt', 'Institut Streichinstrumente und Harfe'),
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
    (13, ['Anna', 'Lukas', 'Clara', 'Jakob', 'Paul', 'Lea', 'Nora', 'Felix', 'Hannah', 'Emil', 'Ida', 'Leon', 'Greta', 'Moritz', 'Svea', 'Oskar', 'Maja', 'Frieda', 'Vincent', 'Marlene',
           'Mia', 'Ben', 'Emma', 'Finn', 'Sophie', 'Elias', 'Lina', 'Noah', 'Johanna', 'Theo', 'Charlotte', 'Anton', 'Helena', 'Karl', 'Mathilda', 'Julius', 'Paula', 'Henri', 'Luise', 'Jonathan'],
     ['Müller', 'Schmidt', 'Fischer', 'Hoffmann', 'Lehmann', 'Wagner', 'Richter', 'Klein', 'Wolf', 'Schröder', 'Schwarz', 'Zimmermann', 'Braun', 'Hartmann', 'Lange', 'Werner', 'Krause', 'Meier', 'Becker', 'Schulz', 'Koch', 'Bauer', 'Schäfer', 'Schmitt', 'Meyer', 'Walter', 'Graf', 'Kaiser', 'Sommer', 'Ernst', 'Kühn',
     'Nagel', 'Stein', 'Jäger', 'Otto', 'Simon', 'Berger', 'Frank', 'Thiele', 'Sauerbrey', 'Reinhardt', 'Ullrich', 'Kretschmer', 'Köhler', 'Herrmann', 'König', 'Fuchs', 'Peters', 'Möller', 'Jung', 'Hahn', 'Vogel', 'Friedrich', 'Günther', 'Winkler', 'Lorenz', 'Baumann', 'Franke', 'Schuster', 'Ludwig', 'Böhm', 'Winter', 'Seidel', 'Haas', 'Dietrich', 'Ziegler', 'Kuhn', 'Pohl', 'Busch', 'Bergmann', 'Voigt', 'Arnold', 'Pfeiffer'],
     '000', ['Weimar', 'Erfurt', 'Jena', 'Leipzig', 'Dresden', 'Berlin', 'Hamburg', 'München', 'Köln', 'Halle (Saale)', 'Gotha', 'Kassel', 'Nürnberg']),
    (2.5, ['Mei Lin', 'Yuxuan', 'Hao', 'Xinyi', 'Zihan', 'Jiayi', 'Yiming', 'Ruoxi', 'Haoran', 'Shuang', 'Wenjie', 'Qing'],
     ['Chen', 'Wang', 'Li', 'Zhang', 'Liu', 'Zhao', 'Huang', 'Zhou', 'Wu', 'Xu', 'Sun', 'Ma'], '479', ['Shanghai', 'Peking', 'Chengdu', 'Wuhan']),
    (2, ['Hyun-woo', 'Ji-ho', 'Seo-yeon', 'Min-jun', 'Ha-eun', 'Ji-woo', 'Soo-ah', 'Do-yun'], ['Park', 'Kim', 'Lee', 'Choi', 'Yoon', 'Jung', 'Kang', 'Cho'], '467', ['Seoul', 'Busan', 'Daegu']),
    (1, ['Yuki', 'Aiko', 'Kaito', 'Haruka', 'Ren', 'Mio', 'Riku'], ['Tanaka', 'Sato', 'Suzuki', 'Takahashi', 'Ito', 'Yamamoto', 'Nakamura', 'Kobayashi'], '442', ['Tokio', 'Osaka', 'Nagoya']),
    (1, ['Zofia', 'Tomasz', 'Marta', 'Jakub', 'Aleksandra', 'Julia', 'Piotr', 'Katarzyna'], ['Nowak', 'Mazur', 'Krawczyk', 'Kaczmarek', 'Wójcik'], '152', ['Krakau', 'Warschau', 'Breslau']),
    (1, ['Chiara', 'Matteo', 'Valentina', 'Lorenzo', 'Giulia', 'Francesca', 'Alessandro', 'Sofia'], ['Rossi', 'Bianchi', 'Romano', 'Ricci', 'Marino', 'Colombo', 'Greco', 'Conti'], '137', ['Mailand', 'Rom', 'Turin', 'Bologna']),
    (1, ['Daniil', 'Sofiia', 'Anastasiia', 'Artem', 'Kateryna', 'Oleksandr', 'Iryna', 'Yuliia'], ['Shevchenko', 'Bondarenko', 'Kovalenko', 'Tkachenko', 'Melnyk', 'Boyko', 'Kravchenko'], '166', ['Kyjiw', 'Lwiw', 'Odesa']),
    (1, ['Lena', 'Florian', 'Magdalena', 'Sebastian', 'Theresa', 'Lorenz'], ['Gruber', 'Huber', 'Pichler', 'Steiner', 'Moser', 'Hofer'], '151', ['Wien', 'Graz', 'Salzburg', 'Linz']),
    (0.5, ['Camille', 'Louis', 'Léa', 'Hugo', 'Manon', 'Gabriel'], ['Moreau', 'Laurent', 'Lefebvre', 'Garnier', 'Bernard'], '129', ['Paris', 'Lyon', 'Straßburg']),
    (0.5, ['Lucía', 'Pablo', 'Carmen', 'Diego', 'Elena', 'Javier'], ['García', 'Fernández', 'López', 'Martínez', 'Sánchez'], '161', ['Madrid', 'Barcelona', 'Valencia']),
    (0.5, ['Emily', 'Michael', 'Sarah', 'Daniel', 'Olivia', 'Ethan'], ['Smith', 'Johnson', 'Miller', 'Davis', 'Wilson'], '368', ['New York', 'Boston', 'Chicago']),
]
INTAKE = {  # applications a year by Hauptfach as of 2026, admission rate, share of the summer round
    ('B.Mus.', 'Streichinstrumente und Harfe'): ({'Violine': 150, 'Viola': 50, 'Violoncello': 80, 'Kontrabass': 30, 'Harfe': 18}, 0.11, 0.25),
    ('B.Mus.', 'Blasinstrumente und Schlagwerk'): (
        {'Flöte': 45, 'Oboe': 25, 'Klarinette': 35, 'Fagott': 18, 'Horn': 28, 'Trompete': 32, 'Posaune': 22, 'Tuba': 9, 'Schlagwerk': 26}, 0.13, 0.2),
    ('B.Mus.', 'Gesang | Musiktheater'): ({'Gesang': 210}, 0.08, 0),
    ('B.Mus.', 'Klavier'): ({'Klavier': 190}, 0.08, 0),
    ('M.Mus.', 'Streichinstrumente'): ({'Violine': 45, 'Viola': 15, 'Violoncello': 25, 'Kontrabass': 8}, 0.29, 0.3),
    ('B.Ed.', 'Musik an Gymnasien (Doppelfach)'): (dict(zip(TEACHING, (22, 18, 10, 10, 6, 6, 5, 4))), 0.43, 0),
    ('M.Ed.', 'Musik an Gymnasien (Doppelfach)'): (dict(zip(TEACHING, (10, 9, 5, 5, 3, 3, 2, 2))), 0.7, 0),
    ('Staatsexamen', 'Lehramt an Gymnasien, Studienfach Musik'): (dict(zip(TEACHING, (20, 16, 9, 9, 6, 6, 5, 4))), 0.41, 0),
    ('Diplom', 'Kirchenmusik A'): ({'Orgel': 7}, 0.54, 0),
    ('Konzertdiplom', 'Konzertexamen'): ({'Klavier': 20, 'Violine': 18, 'Gesang': 12, 'Violoncello': 8, 'Flöte': 4, 'Klarinette': 3, 'Orgel': 2}, 0.22, 0.3),
    ('Dr. phil.', 'Promotion'): ({'Musikwissenschaft': 10, 'Musikpädagogik': 6, 'Kulturmanagement': 4}, 0.54, 0.4),
    ('Zertifikat', 'Thüringer Opernstudio'): ({'Gesang': 60}, 0.16, 0),
}
WINTER_ONLY = {'Horn', 'Posaune', 'Tuba'}  # no summer intake, nor for Gesang and Klavier
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
    for a, b in [('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss'), ('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ś', 's'), ('ł', 'l')]:
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


def area_of(title):
    return next((AREA_OF[start] for start in sorted(AREA_OF, key=len, reverse=True) if title.startswith(start)), None)


def short(form, title):
    """The Lehrform's abbreviation in a plan's form: 'S/Ü 90' → 'S', 'E+x 45' → 'E+x'; a Kolloquium by its title."""
    if 'olloquium' in title:
        return 'K'
    return 'E+x' if form.startswith('E+x') else form[:1]


def lecturer(title):
    return next((key for start, key in sorted(LECTURERS.items(), key=lambda kv: -len(kv[0])) if title.startswith(start)), 'brandauer')


def studied_before(person, study, semesters):
    """A first degree elsewhere for someone starting a postgraduate study `semesters` after they first enrolled: in Germany,
    or abroad for whoever came from there. A generator of its own leaves the rest of the demo as it was."""
    pick, start = random.Random(person.username), study.start_term.start
    person.first_university = person.degree_university = pick.choice(EARLIER) if person.nationality == '000' else destatis.ABROAD + person.nationality
    k = start.year * 2 + (start.month > 6) - semesters
    person.first_term = f'WiSe {k // 2}/{(k // 2 + 1) % 100:02d}' if k % 2 else f'SoSe {k // 2}'
    person.degree_exam = {'M.Mus.': '83', 'M.Ed.': '38'}.get(study.program.degree, '89')
    person.degree_field = study.program.field or study.subject.field
    person.degree_date = start - timedelta(days=pick.randint(60, 100))
    person.degree_grade = pick.choice('1122')


class Command(BaseCommand):
    help = 'Fill an empty database with demo data (fictional people, HfM Weimar structure). Nobody knows the passwords; sign in through the demo profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Empty the database first.')

    def handle(self, *args, reset, **options):
        if reset:
            call_command('flush', interactive=False, verbosity=0)
        elif Person.objects.exists():
            raise CommandError('The database is not empty. Use --reset to replace everything.')
        self.rng = random.Random(1872)  # the year the Hochschule was founded
        self.password = make_password(get_random_string(32))  # one nobody knows: the demo has its access code and profiles
        self.logins = set()
        with transaction.atomic(), history.quietly():
            self.terms()
            self.structure()
            self.people()
            self.students()
            self.teaching()
            self.allocation()
            self.guests()
            self.loans()
            self.exams()
            self.admissions()
            self.practice()
            self.events()
            self.letters()
            forget(months_before(timezone.localdate(), Institution.get().retention_months))  # as the nightly prune would have
        self.changes()
        self.stdout.write(self.style.SUCCESS(
            f'{Person.objects.count()} Personen, {Module.objects.count()} Planeinträge, {Course.objects.count()} Veranstaltungen, '
            f'{Participation.objects.count()} Teilnahmen, {Booking.objects.count()} Buchungen.'
        ))

    # Calendar, plans, rooms

    def terms(self):
        terms = []
        for year in range(2012, 2028):  # from WiSe 2012/13
            april, name = date(year, 4, 1), f'SoSe {year}'
            lectures = LECTURES.get(name, (april + timedelta(days=(2 - april.weekday()) % 7 + 7), date(year, 7, 13)))
            fee = FEES.get(name, FEE if year >= 2022 else Decimal(150 + 5 * (year - 2012)))
            if year > 2012:
                terms.append(Term(name=name, start=april, end=date(year, 9, 30), lectures_start=lectures[0], lectures_end=lectures[1], deadline=date(year, 1, 18), fee=fee))
            if year < 2027:
                october, february, name = date(year, 10, 1), date(year + 1, 2, 1), f'WiSe {year}/{(year + 1) % 100:02d}'
                lectures = LECTURES.get(name, (october + timedelta(days=-october.weekday() % 7), february + timedelta(days=(4 - february.weekday()) % 7)))
                terms.append(Term(name=name, start=october, end=date(year + 1, 3, 31), lectures_start=lectures[0], lectures_end=lectures[1], deadline=date(year, 6, 15), fee=FEES.get(name, fee)))
        for term in terms:
            term.reregistration_start = term.deadline - timedelta(days=14)  # Rückmeldung SoSe 2027: 04.–18.01.2027
        Term.objects.bulk_create(terms)
        self.calendar = list(Term.objects.order_by('start'))
        self.now = next(i for i, t in enumerate(self.calendar) if t.name == 'WiSe 2026/27')
        self.current, self.next = self.calendar[self.now], self.calendar[self.now + 1]

    def structure(self):
        Institution.objects.create(
            name='Hochschule für Musik FRANZ LISZT Weimar', short_name='HfM Weimar', address='Platz der Demokratie 2/3\n99423 Weimar', place='Weimar',
            email='campus@hfm-weimar.de', website='https://www.hfm-weimar.de', imprint='https://www.hfm-weimar.de/footer/impressum',
            privacy='https://www.hfm-weimar.de/footer/datenschutz', accessibility='https://www.hfm-weimar.de/footer/barrierefreiheit',
            insurers='\n'.join(INSURERS), university='2850', region='16055', report_id='28500000',
        )
        Text.objects.bulk_create(Text(key=key, subject=texts.TEXTS[key].subject, body=body) for key, body in TEXTS.items())
        RoomFunction.objects.bulk_create(RoomFunction(key=key, name=name, central=central, order=n) for n, (key, name, central) in enumerate(FUNCTIONS))
        BookingCategory.objects.bulk_create(
            BookingCategory(key=key, name=name, tone=tone, order=n, **settings) for n, (key, name, tone, settings) in enumerate(CATEGORIES)
        )
        self.institutes = {key: Institute.objects.create(name=name) for key, name in INSTITUTES.items()}
        Holiday.objects.bulk_create(Holiday(name=name, first=first, last=last) for name, first, last in HOLIDAYS)
        self.areas = {name: Area.objects.create(name=name, order=n) for n, name in enumerate(AREAS)}
        self.formats = {code: Format.objects.create(name=name, short=code, order=n) for n, (name, code) in enumerate(FORMATS)}
        self.instruments = [
            Instrument.objects.create(number=f'{key}-{n:03d}', name=name, maker=maker, institute=self.institutes[key], note=note, available=available)
            for key, items in INSTRUMENTS.items() for n, (name, maker, note, available) in enumerate(items, 1)
        ]
        self.subjects = {
            name: Subject.objects.create(name=name, institute=self.institutes[SUBJECT_INSTITUTE[name]], english=SUBJECTS_EN[name], field=SUBJECT_FIELDS[name])
            for name in STRINGS + WINDS + ['Gesang', 'Jazz-Gesang', 'Klavier', 'Orgel'] + RESEARCH
        }
        self.programs, self.teach, self.first_course = {}, {}, {}
        for name, degree, version, semesters, subjects, plan in PROGRAMS:
            program = Program.objects.create(
                name=name, degree=degree, version=version, semesters=semesters, postgraduate=degree in POSTGRADUATE, honours=Decimal('1.0'),
                thesis_weeks=THESIS_WEEKS.get(degree), **dict(zip(['qualification', 'english', 'level', 'outcomes'], SUPPLEMENT.get((degree, name), ()))),
                **dict(zip(['exam', 'kind', 'field', 'exam_office'], OFFICIAL[degree])),
            )
            program.subjects.set(self.subjects[s] for s in subjects)
            self.build(program, plan)
            self.programs[(degree, name)] = program
        self.titles = dict(Module.objects.values_list('pk', 'title'))
        for n, (key, name, condition, settings) in enumerate(DOCUMENTS):
            kind = DocumentType.objects.create(key=key, name=name, condition=condition, order=n, **settings)
            if condition == 'subjects':
                kind.subjects.set([self.subjects['Gesang'], self.subjects['Jazz-Gesang']])
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
                self.rooms[name] = Room.objects.create(building=building, name=name, purpose=purpose, function_id=function, capacity=capacity, equipment=equipment)
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
        self.staff, offices = {}, {}
        for name, capabilities in OFFICES.items():
            offices[name] = Office.objects.create(name=name)
            offices[name].permissions.set(Permission.objects.filter(codename__in=capabilities))
            institutes, programs = LIMITS.get(name, ([], []))
            offices[name].institutes.set(self.institutes[key] for key in institutes)
            offices[name].programs.set(self.programs[key] for key in programs)
        for username, title, first, last, office in STAFF:
            self.staff[username] = person = self.person(username, first, last, title)
            person.groups.add(offices[office])
        self.teachers, self.by_subject = {'brandt': self.staff['dekanat']}, defaultdict(list)
        self.staff['dekanat'].institute = self.institutes['MW']
        self.staff['dekanat'].save(update_fields=['institute'])
        for key, title, first, last, subject in TEACHERS:
            institute = self.institutes[SUBJECT_INSTITUTE[subject] if subject else TEACHER_INSTITUTE[key]]
            self.teachers[key] = self.person('lehre' if key == 'wendt' else handle(first, last), first, last, title, institute=institute)
            if subject:
                self.by_subject[subject].append(self.teachers[key])
        self.logins |= set(Person.objects.values_list('username', flat=True))
        self.pools = {  # one-to-one lessons beside the Hauptfach, with Lehrbeauftragte where the plan needs more hours than the staff has
            WS: [self.teachers[k] for k in ('popescu', 'hein', 'martens')] + [self.lecturer('DO') for _ in range(5)],
            PIANO: [self.teachers[k] for k in ('schulte', 'engel')] + [self.lecturer('KL') for _ in range(6)],
            SPEECH: [self.teachers['kessler']] + [self.lecturer('GM') for _ in range(2)],
        }

    def login(self, first, last):
        """A free login name; namesakes get a number."""
        name, n = handle(first, last), 1
        while name in self.logins:
            n += 1
            name = f'{handle(first, last)}{n}'
        self.logins.add(name)
        return name

    def candidate(self, year, age, origin=None, first=None, last=None, username=None):
        """An unsaved person, `age` years old in `year`, named and placed by where they come from."""
        rng = self.rng
        _, firsts, lasts, nationality, places = origin or rng.choices(ORIGINS, weights=[o[0] for o in ORIGINS])[0]
        first, last = first or rng.choice(firsts), last or rng.choice(lasts)
        name = username or self.login(first, last)
        gender = 'd' if rng.random() < 0.004 else 'w' if first in FEMALE else 'm'
        born = date(year - age, rng.randint(1, 12), rng.randint(1, 28))
        german = nationality == '000'
        talent = german and age < 19 and rng.random() < 0.5  # admitted for artistic talent, before the Abitur
        person = Person(
            username=name, first_name=first, last_name=last, password=self.password, email=f'{name}@stud.hfm-weimar.de', gender=gender,
            given_names=f'{first} {rng.choice(MIDDLE[gender])}' if german and gender in MIDDLE and rng.random() < 0.3 else '',
            birth_name=rng.choice(lasts) if age > 24 and gender == 'w' and rng.random() < 0.3 else '',
            birth_date=born, birth_place=rng.choice(places), nationality=nationality,
            second_nationality='000' if not german and rng.random() < 0.15 else '', residence='16055',
            entrance='33' if talent else '03' if german else '39',  # Eignungsprüfung here, Abitur, or a school abroad
            entrance_date=date(year, 2, 20) if talent else date(born.year + 19, 6, rng.randint(20, 30)),
            entrance_region='16055' if talent else KREISE[rng.choice(places)] if german else '99' + nationality,
            address=f'{rng.choice(["Marienstraße", "Steubenstraße", "Trierer Straße", "Humboldtstraße", "Bauhausstraße"])} {rng.randint(1, 60)}\n99423 Weimar',
        )
        person.home = KREISE[person.birth_place] if german else '99' + nationality
        return person

    def newcomer(self, username=None, first=None, last=None, origin=None):
        person = self.candidate(2026, self.rng.randint(19, 26), origin, first, last, username)
        person.save()
        return person

    def panel(self, subject, size=3):
        """Examiners: the subject's teachers first, then colleagues of the same family."""
        own = self.by_subject[subject]
        others = [t for s, ts in self.by_subject.items() if family(s) == family(subject) and s != subject for t in ts]
        return (own + self.rng.sample(others, len(others)))[:size]

    # Students and what they have done

    def students(self):
        """Every application since the first term, and the students they brought. Whoever studies now gets the full record
        (teaching() adds it); the past keeps what the statistics need: who applied, was admitted, graduated or left."""
        rng = self.rng
        chen = next(o for o in ORIGINS if o[3] == '479')
        church = self.programs[('Diplom', 'Kirchenmusik A')]
        self.demo, organist = self.candidate(2024, 20, chen, 'Mei Lin', 'Chen', 'student'), self.candidate(2023, 20)
        self.applicant = self.candidate(2027, 19, ORIGINS[0], 'Jonas', 'Becker', 'bewerbung')
        people, studies = [self.demo, organist, self.applicant], []
        foci = {program: list(program.modules.filter(code__regex=r'^(ME-PR|KM-IV)-')) for program in self.programs.values()}

        def apply(person, program, subject, start, accepted, outcome=None):
            """One application; for whoever was admitted and accepted, the terms they were registered and how it ended."""
            study = Study(person=person, program=program, subject=self.subjects[subject], start_term=self.calendar[start], focus=rng.choice(foci[program] or [None]))
            if program.kind == '5':  # a Promotion; a generator of its own leaves the rest of the demo as it was
                pick = random.Random(person.username)
                study.structured, study.employed, study.cumulative = pick.random() < 0.3, pick.random() < 0.4, pick.random() < 0.2
            study.terms_, study.leave_ = [], None
            if start > self.now:
                study.status, study.since = Study.Status.APPLIED, date(2026, 10, rng.randint(1, 20))
            elif outcome or rng.random() < INTAKE[(program.degree, program.name)][1]:
                if outcome is None and (rng.random() > 0.8 or id(person) in accepted):
                    study.status = Study.Status.LEFT  # admitted, and declined the place
                    study.since = decided(start) + timedelta(days=rng.randint(3, 14))
                    return studies.append(study)
                accepted.add(id(person))
                extra = rng.choices([0, 1, 2, 3, 4], [45, 25, 15, 8, 7])[0] if program.semesters > 2 else 0
                count = program.semesters + extra if outcome or rng.random() > 0.13 else rng.randint(1, max(1, program.semesters - 1))
                last = start + count - 1
                if last + 1 < self.now and count > 2 and rng.random() < 0.05:
                    study.leave_, last = start + rng.randint(1, count - 2), last + 1  # one Urlaubssemester
                study.terms_ = list(range(start, min(last, self.now) + 1))
                if last >= self.now:
                    study.status, study.since = Study.Status.ENROLLED, self.calendar[start].start
                else:
                    study.status = Study.Status.GRADUATED if count >= program.semesters else Study.Status.LEFT
                    study.exit_reason = '1' if count >= program.semesters else random.Random(person.username).choices('64728', [4, 4, 2, 1, 1])[0]
                    study.since = self.calendar[last].end
            else:
                study.status = Study.Status.WAITLISTED if start == self.now and rng.random() < 0.03 else Study.Status.REJECTED
                study.since = decided(start)
            studies.append(study)

        def decided(start):
            term = self.calendar[start]
            return date(term.start.year, 7, rng.randint(6, 15)) if term.name.startswith('WiSe') else date(term.start.year, 2, rng.randint(1, 10))

        apply(self.demo, self.bmsh, 'Violine', self.now - 4, set(), 'enrolled')
        apply(organist, church, 'Orgel', self.now - 6, set(), 'enrolled')
        apply(organist, self.bmkl, 'Klavier', self.now - 4, set(), 'enrolled')  # studies Kirchenmusik and Klavier side by side
        apply(self.applicant, self.bmsh, 'Violoncello', self.now + 1, set())
        for start, term in enumerate(self.calendar[:self.now + 2]):
            summer, back = term.name.startswith('SoSe'), 1 + 0.013 * (2026 - term.start.year)  # a little more each year back
            wanted = [
                (program, subject)
                for (degree, name), (subjects, rate, sose) in INTAKE.items() for program in [self.programs[(degree, name)]]
                for subject, per_year in subjects.items() if not (summer and subject in WINTER_ONLY)
                for _ in range(round(per_year * (sose if summer else 1 - sose) * back * rng.gauss(1, 0.12)))
            ]
            applied, by_subject, accepted = [], defaultdict(set), set()
            for program, subject in rng.sample(wanted, len(wanted)):
                level = program.degree in POSTGRADUATE
                others = [p for p, l in applied[-30:] if l == level and id(p) not in by_subject[subject]]  # some apply for two places
                if others and rng.random() < 0.12:
                    person = rng.choice(others)
                else:
                    person = self.candidate(term.start.year, rng.randint(26, 34) if program.degree == 'Dr. phil.' else rng.randint(22, 28) if level else rng.randint(18, 23))
                    people.append(person)
                applied.append((person, level))
                by_subject[subject].add(id(person))
                apply(person, program, subject, start, accepted)
        current = [s for s in studies if s.start_term == self.current and s.status == Study.Status.ENROLLED and s.person not in (self.demo, organist)]
        for study in current[:2]:  # accepted, not enrolled yet
            study.status, study.terms_ = Study.Status.ADMITTED, []
        late = next(s for s in current[2:] if s.program.degree == 'B.Mus.')  # offered from the Nachrückliste in October, never accepted
        late.status, late.since, late.terms_ = Study.Status.ADMITTED, date(2026, 10, 5), []
        behind = next(s for s in studies if s.start_term == self.current and (s.program, s.subject) == (late.program, late.subject) and s.status == Study.Status.REJECTED)
        behind.status = Study.Status.WAITLISTED
        self.graduate = next(  # finished the B.Mus. in time last summer, and studied nothing else here: the demo's alumna or alumnus
            s for s in studies if s.status == Study.Status.GRADUATED and s.program == self.bmsh and s.terms_[-1] == self.now - 1
            and len(s.terms_) == s.program.semesters and s.leave_ is None and sum(o.person is s.person for o in studies) == 1
        )
        self.graduate.person.username = 'absolvent'

        # Matrikelnummer and health insurance came with the first enrolment, in the order people came
        number = Institution.get().matriculation_start
        for study in sorted((s for s in studies if s.terms_ or s.status == Study.Status.ADMITTED), key=lambda s: s.start_term.start):
            person = study.person
            if person.insurer:
                continue
            person.insurer, person.insurance = rng.choice(INSURERS), Person.Insurance.STATUTORY if rng.random() < 0.9 else Person.Insurance.EXEMPT
            person.insurance_confirmed = study.start_term.start - timedelta(days=rng.randint(20, 50))
            if study.status == Study.Status.ADMITTED:
                person.insurance_confirmed = date(2026, 9, 28) if study is current[0] else None  # one insurer has reported, the other not yet
                continue
            person.matriculation, number = str(number), number + 1
            if study.program.degree in POSTGRADUATE:
                semesters = rng.choice([6, 8, 10])  # earlier degree elsewhere
                person.external_terms = semesters if person.nationality == '000' else 0  # only German semesters count
                studied_before(person, study, semesters)
        Person.objects.bulk_create(people, batch_size=2000)

        # Hauptfach classes of at most fourteen: Lehrbeauftragte take whoever the professors cannot
        enrolled = [s for s in studies if s.status in (Study.Status.ENROLLED, Study.Status.ADMITTED)]
        for subject, teachers in self.by_subject.items():
            size = sum(1 for s in enrolled if s.subject.name == subject)
            for _ in range(-(-size // 14) - len(teachers)):
                teachers.append(self.lecturer(SUBJECT_INSTITUTE[subject]))
        load = defaultdict(int)
        supervisors = {'Musikwissenschaft': 'voss', 'Musikpädagogik': 'neumann', 'Kulturmanagement': 'hausmann'}
        for study in studies:
            subject = study.subject.name
            if study.person is self.demo:
                study.teacher = self.teachers['wendt']
            elif subject in supervisors:
                study.teacher = self.teachers[supervisors[subject]]
            elif study.status in (Study.Status.ENROLLED, Study.Status.ADMITTED):
                study.teacher = min(self.by_subject[subject], key=lambda t: (load[t.pk], rng.random()))
                load[study.teacher.pk] += 1
            else:  # the wish in the application
                study.teacher = rng.choice(self.by_subject[subject])
        Study.objects.bulk_create(studies, batch_size=2000)
        StudyTerm.objects.bulk_create(  # paid at enrolment, then within each Rückmeldung period
            (StudyTerm(
                study=study, term=term, leave=k == study.leave_,
                leave_reason=random.Random(study.person.username).choice('41729') if k == study.leave_ else '',
                paid=(term.start - timedelta(days=rng.randint(20, 60))) if k == study.terms_[0] else term.deadline - timedelta(days=rng.randint(0, 14)),
            ) for study in studies for k in study.terms_ for term in [self.calendar[k]]),
            batch_size=2000,
        )
        StudyTerm.objects.bulk_create(StudyTerm(study=study, term=self.current) for study in current[:2])
        self.enrolled, self.ability, self.rounds = [], {}, defaultdict(list)
        for study in studies:
            if study.status == Study.Status.ENROLLED:
                study.fs = self.now - study.terms_[0] + 1
                self.ability[study.pk] = 1.6 if study.person is self.demo else rng.uniform(1.0, 2.6)
                self.enrolled.append(study)
            if study.start_term in (self.current, self.next):
                self.rounds[study.start_term].append(study)
        self.graduate.fs, self.ability[self.graduate.pk] = self.graduate.program.semesters + 1, 1.8

    def lecturer(self, institute):
        """A Lehrbeauftragte:r of an Institut."""
        _, firsts, lasts, *_ = self.rng.choices(ORIGINS, weights=[o[0] for o in ORIGINS])[0]
        first, last = self.rng.choice(firsts), self.rng.choice(lasts)
        login = self.login(first, last)
        return self.person(login, first, last, email=f'{login}@hfm-weimar.de', institute=self.institutes[institute])

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
            if kind in (Course.Kind.COURSE, Course.Kind.LESSON):
                fields['area'] = self.areas.get(area_of(title))
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
            course = self.course(term, f'{label} · Klasse {study.teacher.last_name}', Course.Kind.LESSON, subject=study.subject, hours=sws(form), format=self.formats['E'])
            return course, study.teacher, LESSON_ROOMS[subject]
        if taught in (WS, PIANO, SPEECH):
            teacher = self.pools[taught][study.pk % len(self.pools[taught])]
            label = {WS: 'Werkstudium', PIANO: 'Klavier (Pflichtfach)', SPEECH: 'Bühnensprechen'}[taught]
            return self.course(term, f'{label} · {teacher.first_name} {teacher.last_name}', Course.Kind.LESSON, hours=sws(form), format=self.formats['E']), teacher, LESSON_ROOMS[taught]
        now = term == self.current  # enrolment from early September to the second week of lectures
        course = self.course(
            term, title, hours=sws(form) if form else None, format=self.formats.get(short(form or '', title)),
            opens=date(2026, 9, 7) if now else None, deadline=date(2026, 10, 16) if now else None,
        )
        course.form = form
        return course, self.teachers[lecturer(title)], None

    def teaching(self):
        """Past results that make up each transcript, and this term's participations, lessons and bookings."""
        rng = self.rng
        self.courses, links, staff, rows, scores = {}, set(), set(), {}, []
        self.lessons, booked = [], set()  # weekly one-to-one lessons this term: (person, course, teacher, rooms, minutes)
        for study in [*self.enrolled, self.graduate]:  # and the graduate's whole record
            subject, enrolled = study.subject.name, study.status == Study.Status.ENROLLED
            behind = enrolled and study.fs > study.program.semesters and rng.random() < 0.6  # over the Regelstudienzeit: the last semester still open
            for leaf in self.plan(study):
                if leaf.semester > study.fs or (behind and leaf.semester == study.program.semesters):
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
                    grade, index = self.grade(study), self.calendar.index(term)
                    if enrolled and study.person != self.demo and rng.random() < 0.06:  # failed; passed on retake the term after, or retaken this term
                        rows[(course.pk, study.pk, leaf.pk)] = Participation(course=course, study=study, module=leaf, grade=Decimal('5.0'), passed=False, created=at(term.start, hour=9))
                        scores += [(course.pk, study.pk, leaf.pk, examiner, Decimal('5.0')) for examiner in examiners]
                        staff |= {(course.pk, e.pk) for e in examiners}
                        if index + 1 == self.now:
                            links.add((course.pk, leaf.pk))
                            continue
                        term = self.calendar[index + 1]
                        course = self.course(term, name, Course.Kind.EXAM, subject=study.subject if artistic else None, released=at(term.end - timedelta(days=45), hour=12))
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
        takers = defaultdict(set)  # who sits in each course this term, so the week is planned around them
        for p in rows.values():
            if p.course.term_id == self.current.pk:
                takers[p.course_id].add(p.study.person_id)
        self.schedule(takers)

    def allocation(self):
        """How places are given: lessons by their teachers, two electives by choosing among requests (the demo student
        waits for one), Kammermusik in ensembles, and Liturgik as a block seminar on three Mondays."""
        now = Course.objects.filter(term=self.current)
        now.filter(models.Q(kind=Course.Kind.LESSON) | models.Q(format=self.formats['E+x'])).update(allocation=Course.Allocation.STAFF)
        notes = ['Ich spiele im Februar Probespiele und möchte vorher Auftrittspraxis sammeln.', '', 'Ich hatte mich im Sommer schon angemeldet.', '']
        for title in ['Auftrittstraining', 'Alexander-Technik']:
            course = now.filter(title=title).first()
            if course is None:
                continue
            course.allocation = Course.Allocation.CHOICE
            course.save(update_fields=['allocation'])
            taken = set(course.participations.values_list('study', flat=True))
            programs = set(course.modules.values_list('program', flat=True))
            wishing = [s for s in self.enrolled if s.program_id in programs and s.pk not in taken and s.person != self.demo][:3]
            if title == 'Auftrittstraining' and self.demo_study().pk not in taken:
                wishing.append(self.demo_study())
            Signup.objects.bulk_create(
                Signup(course=course, study=s, module=course.modules.filter(program=s.program).first(), note=notes[n % 4], created=at(date(2026, 10, 12 + n), hour=9 + n))
                for n, s in enumerate(wishing)
            )
        self.ensembles(now.filter(title='Kammermusik').first())
        block = now.filter(title='Liturgik und Hymnologie').first()
        if block:
            block.bookings.all().delete()
            block.description = 'Blockveranstaltung an drei Montagen, jeweils 9 bis 14 Uhr.'
            block.save(update_fields=['description'])
            teacher = block.staff.first() or self.staff['asa']
            for room in [self.rooms[name] for name in ('PA 311', 'PA 112', 'PA 312', 'HzH 313')]:  # the first room free on all three days
                days = [
                    Booking(room=room, start=at(day, hour=9), end=at(day, hour=14), person=teacher, course=block, category_id='unterricht')
                    for day in [date(2026, 11, 16), date(2026, 12, 7), date(2027, 1, 18)]
                ]
                if not any(b.clashes().exists() for b in days):
                    Booking.objects.bulk_create(days)
                    break

    def ensembles(self, chamber):
        """Kammermusik's students in the usual groupings, registered from September; the latest still without a coach."""
        if chamber is None:
            return
        chamber.allocation = Course.Allocation.ENSEMBLE
        chamber.save(update_fields=['allocation'])
        coaches = {family: self.by_subject[subject][0] for family, subject in [('strings', 'Violoncello'), ('piano', 'Klavier'), ('winds', 'Horn'), ('voice', 'Gesang')]}
        coaches['strings'] = self.teachers['wendt']
        chamber.staff.add(*coaches.values())
        pools = defaultdict(list)
        for p in chamber.participations.select_related('study__subject').order_by('created'):
            pools[p.study.subject.name].append(p.study)
        demo = self.demo_study()
        for pool in pools.values():
            pool[:] = sorted(dict.fromkeys(pool), key=lambda s: s != demo)  # the demo student plays in the first quartet
        groups, formed = [], True
        while formed:  # one of each grouping in turn, so that every kind is played
            formed = False
            for name, parts, family, repertoire in ENSEMBLES:
                if all(len(pools[part]) >= parts.count(part) for part in parts):
                    groups.append((name, [pools[part].pop(0) for part in parts], family, repertoire))
                    formed = True
        rest = [s for pool in pools.values() for s in pool]
        groups += [  # the rest in mixed trios, named by what they play
            ('Ensemble ' + '/'.join(s.subject.name for s in rest[k:k + 3]), rest[k:k + 3], 'strings', 'Nach Absprache') for k in range(0, len(rest), 3)
        ]
        for k, (name, members, family, repertoire) in enumerate(groups):
            open_ = k >= len(groups) - 3  # registered last, coach not yet given
            group = Ensemble.objects.create(
                course=chamber, name=name, repertoire=repertoire, wish=coaches[family] if k % 3 == 0 else None,
                coach=None if open_ else coaches[family], created=at(date(2026, 9, 8) + timedelta(days=k * 38 // len(groups)), hour=10 + k % 8),
            )
            group.members.set(members)

    def loans(self):
        """The Instrumentenpool this semester: second and bigger instruments for students, lent until the end of term; the demo
        student a viola for her Zweitinstrument, one person long overdue, a few back from the summer."""
        free = defaultdict(list)
        for instrument in self.instruments:
            if instrument.available:
                free[instrument.name].append(instrument)
        used, loans = {self.demo}, []

        def lend(name, person, start, due, returned=None, note=''):
            loans.append(Loan(instrument=free[name].pop(0), person=person, start=start, due=due, returned=returned, note=note))

        def borrower(test):
            study = next(s for s in self.enrolled if s.person not in used and test(s))
            used.add(study.person)
            return study.person

        lend('Viola 41 cm', self.demo, date(2026, 10, 6), self.current.end, note='Für das Zweitinstrument')
        lend('Keyboard 61 Tasten', self.late, date(2026, 4, 9), date(2026, 9, 30))
        for n, (name, test) in enumerate([
            ('Kontrabass 3/4', lambda s: s.subject.name == 'Kontrabass'), ('Violoncello 4/4', lambda s: s.subject.name == 'Violoncello'),
            ('Fagott', lambda s: s.subject.name == 'Fagott'), ('Bassklarinette', lambda s: s.subject.name == 'Klarinette'),
            ('Wagnertuba in B', lambda s: s.subject.name == 'Horn'), ('Barockvioline', lambda s: s.subject.name == 'Violine'),
            ('Konzertgitarre', lambda s: s.program.degree == 'B.Ed.'), ('Ukulele (Sopran)', lambda s: s.program.degree == 'B.Ed.'),
            ('Cajón', lambda s: s.program.degree == 'M.Ed.'),
        ]):
            lend(name, borrower(test), date(2026, 10, 1) + timedelta(days=n), self.current.end)
        for name in ['Konzertgitarre', 'Ukulele (Sopran)']:  # back from the summer
            instrument = free[name][0]
            loans.append(Loan(instrument=instrument, person=borrower(lambda s: s.program.degree == 'B.Ed.'), start=date(2026, 4, 13), due=date(2026, 9, 30), returned=date(2026, 9, 24)))
        Loan.objects.bulk_create(loans)

    def demo_study(self):
        return next(s for s in self.enrolled if s.person == self.demo)

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
        self.late, library = self.rng.sample(others, 2)  # one keeps a lent instrument too long (loans()), one owes the library
        library.hold = 'Bibliotheksgebühren offen'
        library.save(update_fields=['hold'])
        for person in self.rng.sample(others, 2):  # changed insurer; the new one has not reported yet
            person.insurer, person.insurance_confirmed = 'BARMER' if person.insurer != 'BARMER' else 'AOK PLUS', None
            person.save(update_fields=['insurer', 'insurance_confirmed'])

    def schedule(self, takers):
        """Weekly bookings across the lecture period: group courses first, each where the fewest of its students already
        have a course, then the one-to-one lessons around each student's week. No room, teacher or student is booked twice."""
        rng, busy, patterns = self.rng, defaultdict(list), []

        def free(keys, start, end):
            return all(e <= start or end <= s for key in keys for s, e in busy[key])

        def take(keys, start, end):
            for key in keys:
                busy[key].append((start, end))

        groups = [c for c in self.courses.values() if c.term_id == self.current.pk and c.kind == Course.Kind.COURSE and c.form[:1] in ('V', 'S', 'Ü', 'G')]
        bookings = []
        for course in sorted(groups, key=lambda c: c.title not in FIXED):
            teacher = course.staff.first()
            if course.title in PHASES:
                room, first, days, start, minutes = PHASES[course.title]
                bookings += [
                    Booking(room=self.rooms[room], start=at(first + timedelta(days=n), clock(start)), end=at(first + timedelta(days=n), clock(start) + minutes),
                            person=teacher, course=course, category_id='orchesterprobe')
                    for n in range(days)
                ]
                continue
            people = takers[course.pk]
            if course.title in FIXED:
                room, day, start, minutes = FIXED[course.title]
                start = clock(start)
            else:
                minutes = length(course.form)
                slots = [(day, start) for day in rng.sample(range(5), 5) for start in (9 * 60 + 15, 11 * 60 + 15, 13 * 60 + 15, 15 * 60 + 15, 17 * 60 + 15)]
                clashes = {(day, start): sum(not free([(p, day)], start, start + minutes) for p in people) for day, start in slots}
                found = min((
                    (room, day, start) for day, start in slots
                    for room in SEMINAR_ROOMS if free([(room, day), (teacher.pk, day)], start, start + minutes)
                ), key=lambda o: clashes[o[1:]], default=None)
                if found is None:
                    continue
                room, day, start = found
            take([(room, day), (teacher.pk, day), *((p, day) for p in people)], start, start + minutes)
            patterns.append((room, day, start, minutes, teacher, course, None))
        for person, course, teacher, rooms, minutes in self.lessons:
            days = [(teacher.pk + k) % 5 for k in range(5)]
            found = next((
                (room, day, start) for day in days for start in range(9 * 60, 20 * 60 - minutes + 1, 15) for room in rooms
                if free([(room, day), (teacher.pk, day), (person.pk, day)], start, start + minutes)
            ), None)
            if found is None:
                continue
            room, day, start = found
            take([(room, day), (teacher.pk, day), (person.pk, day)], start, start + minutes)
            patterns.append((room, day, start, minutes, teacher, course, person))  # the teacher books, the student is entered
        attending, free = [], Holiday.days(self.current.lectures_start, self.current.lectures_end)
        for room, day, start, minutes, person, course, student in patterns:
            first = LATER.get(course.title, self.current.lectures_start)
            series, d = uuid.uuid4(), first + timedelta(days=(day - first.weekday()) % 7)
            while d <= self.current.lectures_end:
                if d not in free:
                    category = 'probe' if course.title == 'Hochschulchor' else 'unterricht'
                    bookings.append(Booking(room=self.rooms[room], start=at(d, start), end=at(d, start + minutes), person=person, course=course, series=series, category_id=category))
                    if student:
                        attending.append((bookings[-1], student))
                d += timedelta(weeks=1)
        Booking.objects.bulk_create(bookings)
        Booking.participants.through.objects.bulk_create(Booking.participants.through(booking_id=b.pk, person_id=s.pk) for b, s in attending)
        meeting = timezone.localdate()
        Booking.objects.create(room=self.rooms['FH 115'], start=at(meeting, hour=14), end=at(meeting, 15 * 60 + 30), person=self.staff['dekanat'], title='Prüfungsausschuss', category_id='sitzung')

    # This term's exams: registration 01.–15.11.2026, exam period 08.–26.02.2027

    def exams(self):
        rng, now = self.rng, self.current
        taken = set(Participation.objects.filter(course__term=now).values_list('study_id', 'module__parent_id'))
        due, theses = defaultdict(list), []
        for study in self.enrolled:
            for leaf in Module.objects.filter(program=study.program, exam=True, semester=study.fs):
                if (study.pk, leaf.parent_id) not in taken or leaf.description == f'entfällt für {study.subject}':  # only exams of modules the study is doing
                    continue
                if leaf.title.startswith(THESES):
                    theses.append((study, leaf))
                    continue
                artistic = leaf.title.startswith(ARTISTIC)
                due[(self.titles[leaf.parent_id], leaf.title, study.subject.name if artistic else '')].append((study, leaf))
        stages, written = ['Festsaal', 'Saal Am Palais', 'FH 106'], ['HzH 313', 'HzH 316', 'HzH 320']
        free = defaultdict(int)  # the next free exam day of each stage
        for n, ((module, title, subject), entries) in enumerate(sorted(due.items())):
            course = Course.objects.create(
                term=now, kind=Course.Kind.EXAM, title=f'{module} · {title}' + (f' · {subject}' if subject else ''),
                subject=self.subjects.get(subject), deadline=date(2026, 11, 15),
            )
            course.staff.set(self.panel(subject) if subject else [self.teachers[lecturer(self.first_course.get(entries[0][1].parent_id, ''))]])
            course.modules.set({leaf for _, leaf in entries})
            room = self.rooms[(stages if subject else written)[n % 3]]
            if subject:
                slots = []
                for k in range(0, len(entries), 12):
                    start = at(EXAM_DAYS[free[room] % len(EXAM_DAYS)], hour=9)
                    free[room] += 1
                    slots += [start + timedelta(minutes=30 * j) for j in range(len(entries[k:k + 12]))]
                    end = slots[-1] + timedelta(minutes=30)
                    if not Booking(room=room, start=start, end=end).clashes().exists():
                        Booking.objects.create(room=room, start=start, end=end, person=self.staff['pruefungsamt'], course=course, category_id='pruefung')
            else:
                start = at(EXAM_DAYS[(n // 3) % len(EXAM_DAYS)], hour=9 + (n % 3) * 3)
                slots = [start] * len(entries)
                if not Booking(room=room, start=start, end=start + timedelta(minutes=120)).clashes().exists():
                    Booking.objects.create(room=room, start=start, end=start + timedelta(minutes=120), person=self.staff['pruefungsamt'], course=course, category_id='pruefung')
            Participation.objects.bulk_create(
                Participation(
                    course=course, study=study, module=leaf, slot=slot,
                    note=self.repertoire(study.subject.name) if subject else '', created=at(date(2026, 11, rng.randint(1, 14)), hour=10),
                )
                for slot, (study, leaf) in zip(slots, entries)
            )
        self.theses(theses)
        demo_study = self.demo_study()
        owed = Module.objects.get(program=demo_study.program, parent__code='BM-XX-MW-01', exam=True)
        if not Course.objects.filter(term=now, modules=owed).exists():  # the exam Mei Lin still owes is on offer this term
            course = Course.objects.create(term=now, kind=Course.Kind.EXAM, title=f'Musikgeschichte I · {owed.title}', deadline=date(2026, 11, 15))
            course.staff.set([self.teachers['voss']])
            course.modules.set(Module.objects.filter(parent__code='BM-XX-MW-01', exam=True))
            start = at(date(2027, 2, 10), hour=10)
            Booking.objects.create(room=self.rooms['HzH 316'], start=start, end=start + timedelta(hours=3), person=self.staff['pruefungsamt'], course=course, category_id='pruefung')
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
            Booking.objects.create(room=self.rooms['FH 106'], start=start, end=start + timedelta(hours=1), person=self.staff['pruefungsamt'], course=resit, category_id='pruefung')
            for k, p in enumerate(failed):
                p.grade, p.passed = Decimal('5.0'), False
                p.save(update_fields=['grade', 'passed'])
                Score.objects.filter(participation=p).update(value=Decimal('5.0'))
                retry = Participation.objects.create(course=resit, study=p.study, module=p.module, slot=start + timedelta(minutes=30 * k), note=self.repertoire(p.study.subject.name))
                Score.objects.bulk_create(Score(participation=retry, examiner=e, value=rng.choice(NOTES[1:5])) for e in examiners[:2])

    def theses(self, theses):
        """Topics given out this October, due within the Prüfungsordnung's weeks; one handed in early, half marked: a task for the demo teacher."""
        rng, wendt, readers = self.rng, self.teachers['wendt'], [self.teachers[k] for k in ('voss', 'brandauer', 'roth')]
        made = []
        for study, leaf in theses:
            given = date(2026, 10, rng.randint(1, 20))
            first = study.teacher if study.program.degree in ('B.Mus.', 'M.Mus.', 'Diplom') else self.teachers['neumann']
            course = Course.objects.create(
                term=self.current, kind=Course.Kind.THESIS, title=rng.choice(TOPICS).format(i=study.subject.name),
                deadline=given + timedelta(weeks=study.program.thesis_weeks or 12),
            )
            course.staff.set([first, rng.choice([r for r in readers if r != first])])
            course.modules.set([leaf])
            made.append(Participation.objects.create(course=course, study=study, module=leaf, created=at(given, hour=10)))
        early = next((p for p in made if p.study.program == self.bmsh), None)
        if early:
            second = early.course.staff.exclude(pk=early.study.teacher_id).first()
            early.course.staff.set([wendt, second])
            Document.objects.create(
                person=early.study.person, study=early.study, kind=DocumentType.system('abschlussarbeit'), title='Abschlussarbeit.pdf',
                url=f'https://upload.hfm-weimar.de/{uuid.uuid4().hex[:10]}.pdf', created=at(date(2026, 10, 22), hour=16),
            )
            Score.objects.create(participation=early, examiner=second, value=Decimal('1.7'))

    def repertoire(self, subject):
        return '\n'.join(w.format(i=subject) for w in self.rng.sample(REPERTOIRE[family(subject)], 3))

    # Admissions: the WiSe intake is decided, the SoSe intake is being marked

    def admissions(self):
        """The WiSe intake is decided, the SoSe intake is being marked; auditions where the Hochschule holds them."""
        auditioned = ('B.Mus.', 'M.Mus.')
        for term, video_deadline, audition_day in [(self.current, date(2026, 3, 15), date(2026, 5, 29)), (self.next, date(2026, 11, 2), date(2027, 1, 13))]:
            outcomes = [(s, 'running' if s.status == Study.Status.APPLIED else s.status) for s in self.rounds[term] if s.program.degree in auditioned]
            self.intake(term, outcomes, video_deadline, audition_day, finished=term == self.current)

    def intake(self, term, outcomes, video_deadline, audition_day, finished):
        """Audition courses of one intake and every applicant's way through them."""
        rng, applicant = self.rng, self.applicant
        closed = at(audition_day + timedelta(days=35), hour=12) if finished else None
        written = audition_day + timedelta(days=2)
        written += timedelta(days=(7 - written.weekday()) % 7 if written.weekday() > 4 else 0)  # never on a weekend
        shared = []
        for title, keys, room, step in [
            ('Eignungsprüfung Klavier', ['schulte', 'engel'], 'FH 213', 15),
            ('Eignungsprüfung Musiktheorie und Gehörbildung', ['brandauer', 'roth'], 'HzH 313', 0),
        ]:
            course = Course.objects.create(term=term, kind=Course.Kind.AUDITION, title=title, pass_mark=Decimal('11.6'), max_points=25, released=closed)
            course.jury = [self.teachers[k] for k in keys]
            course.staff.set(course.jury)
            course.plan = (self.rooms[room], step)
            shared.append(course)
        accompanist = self.course(term, f'Korrepetition Eignungsprüfung {term}', Course.Kind.LESSON)  # a service, not weekly teaching
        accompanist.staff.set([self.teachers['popescu'], self.teachers['hein']])
        rounds, rows, scores, documents = {}, [], [], []
        names = dict(DocumentType.objects.values_list('key', 'name'))
        summer = term.name.startswith('SoSe')

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
                p.grade = mean(marks, study.program.mean_rounding)
                p.passed = course.passes(p.grade)
            rows.append(p)
            scores.extend((p, e, v) for e, v in zip(marked, marks))
            return p

        for study, outcome in outcomes:
            subject, master = study.subject.name, study.program.degree == 'M.Mus.'
            key = (study.program_id, subject)
            if key not in rounds:
                level, n = (' (Master)' if master else ''), len(rounds)
                decided = finished or subject == 'Violoncello'
                subjects, rate, sose = INTAKE[(study.program.degree, study.program.name)]
                if finished:  # every place taken; a class without a free place took nobody
                    places = sum(1 for s, o in outcomes if (s.program_id, s.subject.name) == key and o in (Study.Status.ENROLLED, Study.Status.ADMITTED))
                else:
                    places = max(1, round(subjects[subject] * rate * (sose if summer else 1 - sose)))
                intake = {'program': study.program, 'deadline': video_deadline, 'capacity': places, 'accompaniment': accompanist}  # the course one applies to
                video = Course.objects.create(
                    term=term, kind=Course.Kind.AUDITION, title=f'Videostufe {subject}{level}', subject=study.subject, video=True, pass_mark=Decimal('16.1'), max_points=25,
                    released=at(video_deadline + timedelta(days=30) if finished else date(2026, 10, 20), hour=12) if decided else None, **intake,
                ) if subject in VIDEO_SUBJECTS else None
                hall = Course.objects.create(
                    term=term, kind=Course.Kind.AUDITION, title=f'Eignungsprüfung Hauptfach {subject}{level}', subject=study.subject,
                    pass_mark=Decimal('19.1' if master else '16.1'), max_points=25, released=closed, **({} if video else intake),
                )
                panel = self.panel(subject)
                hall.staff.set(panel)
                if video:
                    video.staff.set(panel)
                hall.plan = (self.rooms['Festsaal' if n % 2 == 0 else 'Saal Am Palais'], 25)
                rounds[key] = video, hall, panel
            video, hall, panel = rounds[key]
            if outcome == 'running':
                talent = 21.4 if study.person == applicant else rng.uniform(13, 23)
            elif outcome == Study.Status.REJECTED:
                talent = rng.uniform(12, 15.6)
            else:
                talent = rng.uniform(20.5 if master else 18.5, 23.5)
            note = self.repertoire(subject)
            handed = ['cv', 'school'] + ['degree'] * master
            if finished or study.since.day < 15 or study.person == applicant:  # the latest applicants still owe the fee receipt
                handed.append('fee')
            if study.person.nationality != '000' and rng.random() < 0.5:
                handed.append('language')
            if video:
                documents.append(Document(person=study.person, study=study, kind_id='video', title=DEMO_VIDEO[0], url=DEMO_VIDEO[1], created=at(study.since, hour=11)))
            documents += [
                Document(person=study.person, study=study, kind_id=kind, title=names[kind], url=f'https://upload.hfm-weimar.de/{uuid.uuid4().hex[:10]}.pdf', created=at(study.since, hour=11))
                for kind in handed
            ]
            if not video or (sit(study, video, talent + 1.5, panel, note).passed and video.released):  # videos are judged more kindly
                sit(study, hall, talent, panel, note)
                if finished and not master:
                    for course in shared:
                        sit(study, course, rng.uniform(13, 21), course.jury, '')
            if rng.random() < 0.3 or study.person == applicant:
                rows.append(Participation(course=accompanist, study=study, created=at(study.since, hour=12)))
        sitting = defaultdict(list)
        for p in rows:
            sitting[p.course].append(p)
        free = defaultdict(lambda: audition_day)  # the next free audition day of each hall
        for course in [hall for _, hall, _ in rounds.values()] + shared:
            room, step = course.plan
            day = written if course in shared else free[room]
            entries = sitting[course]
            per_day = {25: 16, 15: 24}.get(step) or len(entries) or 1
            for k in range(0, len(entries), per_day):  # a day at a time, weekdays only, the hall booked for each
                day += timedelta(days=(7 - day.weekday()) % 7 if day.weekday() > 4 else 0)
                begin, chunk = at(day, hour=9), entries[k:k + per_day]
                for j, p in enumerate(chunk):
                    p.slot = begin + timedelta(minutes=step * j)
                end = begin + timedelta(minutes=step * len(chunk) or 120)
                if not Booking(room=room, start=begin, end=end).clashes().exists():
                    Booking.objects.create(room=room, start=begin, end=end, person=self.staff['asa'], course=course, category_id='eignungspruefung')
                day += timedelta(days=1)
            if course not in shared:
                free[room] = day
        Participation.objects.bulk_create(rows)
        Score.objects.bulk_create(Score(participation=p, examiner=e, value=v) for p, e, v in scores)
        Document.objects.bulk_create(documents)
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

        usable = ['uebung', 'unterricht', 'orgel']
        rooms = defaultdict(list)
        for room in Room.objects.filter(function__in=usable).prefetch_related('institutes'):
            for institute in room.institutes.all():
                rooms[institute.pk].append(room)
        practising = [s for s in self.enrolled if s.person != self.demo and rooms[s.subject.institute_id]]
        bookings, used = [], defaultdict(float)
        demo = self.rooms['FH 310']
        start = at(today, 16 * 60)
        if free(demo, start, start + timedelta(minutes=90)):
            bookings.append(Booking(room=demo, start=start, end=start + timedelta(minutes=90), person=self.demo, category_id='uebung', created=at(today, 9 * 60 + 12)))
        for offset in range(-6, 2):
            day = today + timedelta(days=offset)
            for study in rng.sample(practising, len(practising) * 4 // 5) * 2:  # most practise, many twice a day, most in the afternoon
                minute = 30 * round(2 * rng.triangular(8, 20.5, 16))
                minutes = rng.choice([60, 90, 120])
                begin, end = at(day, minute), at(day, minute + minutes)
                last = rng.random() < 0.15  # booked within three hours: does not count
                booked = begin - timedelta(minutes=rng.randint(20, 170) if last else rng.randint(4 * 60, 23 * 60))
                if booked > now or (not last and used[(study.person_id, day)] + minutes / 60 > 4):
                    continue
                choices = rooms[study.subject.institute_id]
                room = next((r for r in rng.sample(choices, min(3, len(choices))) if free(r, begin, end)), None)
                if room:
                    bookings.append(Booking(room=room, start=begin, end=end, person=study.person, category_id='uebung', created=booked))
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
                room=room, start=begin, end=begin + timedelta(hours=2), person=members[0].person, category_id='kammermusik',
                title='Kammermusikprobe Klaviertrio' if n % 2 else 'Kammermusikprobe Streichquartett', created=at(today - timedelta(days=rng.randint(0, 3)), hour=10),
            )
            rehearsal.participants.set(m.person for m in members[1:])

    def events(self):
        """What is on in the coming weeks, and what it takes behind the scenes."""
        today, vb, t = timezone.localdate(), self.staff['veranstaltungsbuero'], self.teachers
        plan = [  # days from today, room, start, minutes, category, title, booked for
            (1, 'Festsaal', '08:00', 90, 'stimmung', 'Stimmung Steinway D', vb),
            (2, 'FH 108', '19:30', 120, 'klassenabend', 'Klassenabend Violine · Klasse Prof. Wendt', t['wendt']),
            (3, 'Jenny-Fleischer-Alt-Saal', '19:30', 120, 'konzert', 'Liederabend der Gesangsklassen', t['lindner']),
            (4, 'Herz Jesu', '18:00', 60, 'konzert', 'Orgelvesper', t['sander']),
            (5, 'HzH 304', '20:00', 150, 'konzert', 'Jazz-Session', t['weber']),
            (6, 'FH 108', '10:00', 360, 'meisterkurs', 'Meisterkurs Violoncello', t['reinholdt']),
            (7, 'HzH 313', '19:00', 90, 'vortrag', 'Vortrag: Liszt und die Neudeutsche Schule', t['voss']),
            (8, 'Studiotheater', '19:00', 150, 'abgesagt', 'Opernszenen', t['falk']),
            (9, 'Festsaal', '19:30', 120, 'konzert', 'Kammermusikabend', t['reinholdt']),
            (10, 'Saal Am Palais', '08:00', 480, 'blockiert', 'Wartung der Jehmlich-Orgel', vb),
            (12, 'Saal Am Palais', '15:00', 180, 'option', 'Chorkonzert des Hochschulchors', t['hagen']),
            (16, 'Weimarhalle großer Saal', '09:00', 180, 'transport', 'Instrumente Festsaal → Weimarhalle', vb),
            (16, 'Weimarhalle großer Saal', '14:00', 180, 'orchesterprobe', 'Generalprobe Hochschulorchester', t['ferreira']),
            (16, 'Weimarhalle großer Saal', '19:30', 150, 'konzert', 'Sinfoniekonzert des Hochschulorchesters', t['ferreira']),
            (16, 'Weimarhalle großer Saal', '18:30', 240, 'konzertbetreuung', 'Abenddienst Sinfoniekonzert', vb),
            (21, 'Festsaal', '10:00', 360, 'vermietung', 'Vermietung: Festakt der Stadt Weimar', vb),
            (23, 'Stadtschloss neuer Veranstaltungssaal', '17:00', 90, 'konzert', 'Musik im Schloss', t['brueck']),
        ]
        for offset, name, begin, minutes, category, title, person in plan:
            for day in (today + timedelta(days=offset + k) for k in range(4)):  # the first free day
                booking = Booking(room=self.rooms[name], start=at(day, clock(begin)), end=at(day, clock(begin) + minutes), person=person, category_id=category, title=title)
                if category == 'konzertbetreuung' or not booking.clashes().exists():
                    booking.save()
                    break
            if category == 'klassenabend':
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
        concert = Booking.objects.filter(category='konzert', start__gte=timezone.now()).first()
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
        decided = Study.objects.filter(status__in=list(DECISIONS), start_term__in=[self.current, self.next])
        for study in decided.select_related('person', 'program', 'subject', 'start_term'):
            deadline = study.since + timedelta(days=Institution.get().accept_days)
            notices.append((study, *study.wording(DECISIONS[study.status], frist=f'{deadline:%d.%m.%Y}', datum=f'{study.since:%d.%m.%Y}'), at(study.since, hour=10)))
        for study in (s for s in self.enrolled if s.fs == 1):
            notices.append((study, *study.wording('immatrikulation'), at(study.start_term.start - timedelta(days=10), hour=10)))
        sender = texts.block('absender_bescheide')
        sent = Message.objects.bulk_create(
            Message(person=s.person, subject=title, body=text, sender=sender, created=when, read=when) for s, title, text, when in notices
        )
        Document.objects.bulk_create(Document(person=s.person, study=s, kind_id='notice', title=title, message=m, created=when) for (s, title, _, when), m in zip(notices, sent))
