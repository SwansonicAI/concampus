"""The study plans of HfM FRANZ LISZT Weimar as data.

B.Mus.: Studienverlaufs- und Prüfungspläne (SVPP) of the FPSO B.Mus. 2017 as amended in 2026 (6. Änderung,
Verkündungsblatt 2/2026), in force from 01.10.2026. M.Mus.: FPSO Master of Music (Verkündungsblatt 5/2025),
for students starting from SoSe 2026. Grading follows the RPSO 2026.

Shapes: M = module, G = group or choice of modules, C = course work booked per semester, X = exam, L = an elective
taken as a whole. Course work carries the credits the plan prints per semester; exams carry none but must be
passed, and their weight sets their share of the module grade. A module's weight is its share of the final grade.
"""

HF, WS, PIANO, SPEECH = 'hf', 'ws', 'piano', 'speech'  # one-to-one lessons: main subject, Klavierpartner:in, Pflichtfach Klavier, Sprecherziehung
SECTION, VOCAL, EARLY = 'section', 'vocal', 'early'  # and Orchesterstudien with a teacher of one's instrument, Stimmbildung, historical instruments


def M(code, title, credits, *parts, weight=0, note='', final=False):
    """A module; `final`: part of the Abschlussprüfung."""
    return {'shape': 'M', 'code': code, 'title': title, 'credits': credits, 'parts': parts, 'weight': weight, 'note': note, 'final': final}


def G(code, title, credits, *parts, weight=0, note=''):
    return {'shape': 'G', 'code': code, 'title': title, 'credits': credits, 'parts': parts, 'weight': weight, 'note': note}


def C(title, credits, course=None, form='', taught=None, start=1):
    """Course work: {semester: credits}. `course` is the course title ({n}, {r}: running number), or {'WiSe': …, 'SoSe': …}."""
    return {'shape': 'C', 'title': title, 'credits': credits, 'course': course or title, 'form': form, 'taught': taught, 'start': start}


def X(title, semester, weight=1, form='', waived=(), final=False):
    """An exam; `waived`: the Hauptfächer the plan lets off it; `final`: part of the Abschlussprüfung."""
    return {'shape': 'X', 'title': title, 'semester': semester, 'weight': weight, 'form': form, 'waived': waived, 'final': final}


def L(code, title, credits, course=None, form='', taught=None):
    return {'shape': 'L', 'code': code, 'title': title, 'credits': credits, 'course': course or title, 'form': form, 'taught': taught}


def theory(weight):
    """Musikwissenschaft and Musiktheorie, shared by all B.Mus. plans. The Musikgeschichte exam is taken once."""
    overview = {'WiSe': 'Musikgeschichte im Überblick II', 'SoSe': 'Musikgeschichte im Überblick I'}
    overview2 = {'WiSe': 'Musikgeschichte im Überblick III', 'SoSe': 'Musikgeschichte im Überblick IV'}
    return [
        M('BM-XX-MW-01', 'Musikgeschichte I', 7,
          C('Musikgeschichte 1 und 2', {1: 2, 2: 2}, overview, 'V 90'),
          C('Spezialvorlesung oder Notentext und Interpretation', {2: 2}, '„Pour la fin du temps“ – Jüdisch-christliche Endzeitvorstellungen in der Musik', 'V 90'),
          C('Kontrapunkt 1', {2: 1}, 'Kontrapunkt 1 (vokale Improvisation)', 'G 60'),
          X('Mündliche Prüfung (30 Min.)', 2), weight=weight,
          note='Die Modulprüfung Musikgeschichte wird einmal abgelegt, in Musikgeschichte I oder II.'),
        M('BM-XX-MW-02', 'Musikgeschichte II', 8,
          C('Musikgeschichte 3 und 4', {3: 2, 4: 3}, overview2, 'V 90'),
          C('Formenlehre', {4: 2}, form='Ü 90'),
          C('Satztechniken des 20./21. Jahrhunderts', {3: 1}, 'Satztechniken des 20./21. Jh. 1', 'G 60')),
        M('BM-XX-MT-01', 'Musiktheorie I', 6,
          C('Allgemeine Musiklehre', {1: 2}, form='Ü 60'),
          C('Harmonielehre 1', {1: 1}, 'Gehörbildung 1/Harmonielehre 1', 'G 60'),
          C('Gehörbildung 1', {1: 1}, 'Gehörbildung 1/Harmonielehre 1', 'G 60'),
          C('Instrumentenkunde/Akustik', {1: 2}, form='Ü 90'),
          X('Klausur (90 Min.)', 1), weight=weight),
        M('BM-XX-MT-02', 'Musiktheorie II', 6,
          C('Harmonielehre 2 und 3', {2: 1, 3: 2}, 'Gehörbildung {n}/Harmonielehre {n}', 'G 60', start=2),
          C('Gehörbildung 2 und 3', {2: 1, 3: 2}, 'Gehörbildung {n}/Harmonielehre {n}', 'G 60', start=2),
          X('Klausur Harmonielehre (180 Min.)', 3), X('Klausur Gehörbildung (60 Min.)', 3), X('Mündliche Prüfung (20 Min.)', 3),
          weight=weight),
        M('BM-XX-MT-03', 'Musiktheorie III', 5,
          C('Werkanalyse 1', {4: 3}, 'Höranalyse 1/Werkanalyse 1', 'G 60'),
          C('Höranalyse 1', {4: 2}, 'Höranalyse 1/Werkanalyse 1', 'G 60'),
          X('Hausarbeit Werkanalyse', 4), X('Mündliche Prüfung Höranalyse (20 Min.)', 4), weight=weight),
    ]


def professional_basics(semesters, credits=5):
    """Professionalisierung I: courses chosen to `credits` CP; `semesters` maps each course to its plan semester."""
    pool = [
        ('Musik & Gesundheit 1', 2, 'Musik und Gesundheit 1', 'V+Ü 90'), ('Musik & Gesundheit 2', 2, 'Musik und Gesundheit 2', 'V+Ü 90'),
        ('Körpertechniken', 1, 'Alexander-Technik', 'G'), ('Konditionstraining', 1, 'Konditionstraining', 'G'),
        ('Auftrittstraining', 1, 'Auftrittstraining', 'G 60'), ('Mentales Training', 1, 'Mentales Training', 'G 60'),
        ('Selbstmanagement', 1, 'Selbstmanagement', 'Ü 60'),
    ]
    return [C(title, {semesters[n]: cp}, course, form) for n, (title, cp, course, form) in enumerate(pool)]


def scientific_basics():
    return M('BM-XX-MW-P2', 'Professionalisierung II – Wissenschaftliche Grundlagen', 5,
             C('Textwerkstatt', {5: 1}, 'Schreibwerkstatt: wissenschaftliches Schreiben', 'S/Ü 60'),
             C('Recherchieren und Präsentieren', {5: 2}, 'Einführung in das wissenschaftliche Arbeiten und Schreiben', 'S/Ü 90'),
             C('Künstlerisch-wissenschaftliches Projektseminar', {6: 2}, 'Brasilianische Instrumentalmusik: choro, samba, maxixe', 'S/Ü 90'),
             C('Künstlerisch-wissenschaftliches Kompaktseminar', {5: 1}, 'Musikalische Wirkungsforschung – in der DAW nachgebaut', 'S/Ü 45'),
             C('Wissenschaftliches Seminar oder Vorlesung', {6: 2}, 'Soundtrack der Erinnerung: Musik in den Filmen zum Holocaust', 'S 90'),
             C('Werkanalyse 2', {5: 2}, form='G 60'),
             note='Lehrveranstaltungen im Umfang von 5 CP. „Recherchieren und Präsentieren“ ist bei Instrumentalpädagogischer Vertiefung Pflicht.')


def pedagogy_deepening(code, prefix, fd1, fd2, exams, weight_fd2, weight_mp2=5):
    """The instrumental-pedagogy path shared by the orchestral plans (Musikpädagogik II, Fachdidaktik/Lehrpraxis, EMP, Chor)."""
    return [
        M('BM-XX-IV-01', 'Musikpädagogik II', 6,
          C('Musikpädagogik 2 (Musikpsychologie) und 3 (Instrumentalpädagogik)', {5: 1, 6: 2}, 'Musikpädagogik 3 (Instrumental- und Gesangspädagogik)', 'V 90'),
          C('Musikpädagogisches Orientierungspraktikum (40 Std.)', {5: 2}, 'Musikpädagogisches Orientierungspraktikum', 'Pr'),
          C('Rhetorik/Kommunikation/Stimmbildung', {6: 1}, form='E+x 30'),
          X('Hausarbeit (10 Seiten)', 6), weight=weight_mp2),
        M(f'{code}-01', f'Fachdidaktik/Lehrpraxis{prefix} I', 7, *fd1),
        M(f'{code}-02', f'Fachdidaktik/Lehrpraxis{prefix} II', sum(sum(p['credits'].values()) for p in fd2), *fd2, *exams, weight=weight_fd2),
    ]


def orchestral(x):
    """B.Mus. Streichinstrumente und Harfe (x='SH', Anlage 17) or Blasinstrumente und Schlagwerk (x='BS', Anlage 05)."""
    strings = x == 'SH'
    group = 'Streicher' if strings else 'Bläser'
    main = 'Hauptfach' if strings else 'Hauptinstrument'
    return [
        M(f'BM-{x}-01', 'Künstlerische Präsentation I', 52,
          C(main, {1: 16, 2: 16, 3: 10, 4: 10}, form='E 90', taught=HF),
          C('Werkstudium', {1: 0, 2: 0, 3: 0, 4: 0}, form='E 30', taught=WS),
          X('Künstlerisch-praktische Prüfung (10 Min.)', 1, 0),
          X('Künstlerisch-praktische Prüfung (15 Min.)', 3, 0, 'entfällt für Violine' if strings else '', ['Violine'] if strings else []),
          X('Künstlerisch-praktische Prüfung (20 Min.)', 4, 0),
          note='Über das Bestehen entscheiden alle Lehrenden des Moduls gemeinsam.'),
        M(f'BM-{x}-02', 'Künstlerische Präsentation II', 56,
          C(main, {5: 14, 6: 14, 7: 14, 8: 14}, form='E 90', taught=HF),
          C('Werkstudium', {5: 0, 6: 0, 7: 0, 8: 0}, form='E 30', taught=WS),
          note='Voraussetzung: 100 CP und bestandene Künstlerische Präsentation I.'),
        M('BM-OI-01', 'Instrumentale Berufsvorbereitung I', 16,
          C('Orchester- und Ensemblepraxis', {2: 2, 3: 2, 4: 2}, 'Hochschulorchester', 'G 180'),
          C('Kammermusik', {3: 3, 4: 3}, form='E+x 45'),
          C('Orchesterstudien', {3: 2, 4: 2}, f'Orchesterstudien {group}', 'E 30', SECTION),
          X('Künstlerisch-praktische Prüfung Orchesterstudien (10 Min.)', 4, 0)),
        M('BM-OI-02', 'Instrumentale Berufsvorbereitung II', 6,
          C('Kammermusik', {5: 3, 6: 3}, form='E+x 45'),
          note='Bei Künstlerischer Vertiefung zusätzlich Kammermusik im 7. Semester (3 CP).'),
        M('BM-XX-KL-04', 'Klavier', 8,
          C('Klavier', {1: 2, 2: 2, 3: 2, 4: 2}, form='E 30', taught=PIANO),
          X('Künstlerisch-praktische Prüfung (20 Min.)', 4), weight=5 if strings else 0),
        *theory(6.25),
        M('BM-OI-MP-01', 'Musikpädagogik I', 7,
          C('Musikpädagogik 1', {2: 2}, form='V 90'),
          C('Fachdidaktik 1 und 2', {3: 1, 4: 2}, f'Fachdidaktik {group} {{n}}', 'S/Ü 90'),
          C('Lehrpraxis/Hospitation/Pädagogisches Projekt', {3: 1, 4: 1}, 'Lehrpraxis/Hospitation', 'Ü 45'),
          X('Mündliche Prüfung Musikpädagogik 1 (20 Min.)', 2),
          *([X('Mündliche Prüfung Fachdidaktik (15 Min.)', 4), X('Lehrprobe mit Gespräch (30 + 15 Min.)', 4)] if strings else []),
          weight=5),
        M('BM-XX-BF-P1', 'Professionalisierung I – Berufsfeldbezogene Grundlagen', 5, *professional_basics([1, 2, 1, 3, 1, 2, 1]),
          note='Lehrveranstaltungen im Umfang von 5 CP; Körpertechniken, Konditions-, Auftritts- und Mentales Training bis zu zweimal.'),
        scientific_basics(),
        G('V', 'Vertiefung', 43,
          G('KV', 'Künstlerische Vertiefung', 43,
            M('BM-OI-KV', 'Künstlerische Vertiefung', 28,
              C('Orchester- und Ensemblepraxis', {5: 3, 6: 3, 7: 3}, 'Hochschulorchester', 'G 180'),
              C('Orchesterstudien', {5: 3, 6: 3, 7: 3, 8: 3}, f'Orchesterstudien {group}', 'E 30', SECTION),
              C('Probespielliteratur', {8: 7}, 'Probespieltraining', 'E'),
              X('Künstlerisch-praktische Prüfung Orchesterstudien (15–25 Min.)', 8, 1),
              X('Künstlerisch-praktische Prüfung Probespielliteratur (20–30 Min.)', 8, 2 if strings else 1), weight=15),
            M('BM-OI-02-KV', 'Kammermusik bei Künstlerischer Vertiefung', 3, C('Kammermusik', {7: 3}, form='E+x 45')),
            M('BM-OI-BP-KV', 'Bachelorprojekt bei KV', 12,
              C('Bachelorprojekt', {7: 6, 8: 6}, 'Bachelorprojekt: Kolloquium', 'S'),
              X('Rezital (50 Min.)' if strings else 'Rezital (50 Min., Schlagwerk 60 Min.)', 8, 48 if strings else 53),
              X('Programmheft oder Dokumentation (8–10 Seiten)', 8, 2), weight=50 if strings else 55, final=True),
            weight=1),
          G('IV', 'Instrumentalpädagogische Vertiefung', 43,
            *pedagogy_deepening(
                'BM-OI-IV', '',
                [C('Fachdidaktik 3 und 4', {5: 1, 6: 1}, f'Fachdidaktik {group} {{n}}', 'S/Ü 90', start=3),
                 C('Lehrpraxis', {5: 1, 6: 2}, form='Ü 90'),
                 C('Unterrichtspraktisches Klavierspiel', {5: 1, 6: 1}, form='E+x 45')],
                [C('Fachdidaktik 5', {7: 2}, f'Fachdidaktik {group} 5', 'S/Ü 90'),
                 C('Lehrpraxis', {7: 2, 8: 2}, form='Ü 90'),
                 C('Unterrichtspraktisches Klavierspiel', {7: 1, 8: 2}, form='E+x 45')],
                [X('Mündliche Prüfung Fachdidaktik (20 Min.)', 7), X('Lehrprobe (30 Min.)', 7), X('Lehrprobe (30 Min.)', 8),
                 X('Praktische Prüfung Unterrichtspraktisches Klavierspiel (20 Min.)', 8)],
                weight_fd2=15),
            M('BM-XX-IV-02', 'EMP/Rhythmik', 5, C('Rhythmik', {6: 2}, form='G 60'), C('Elementare Musikpädagogik', {7: 3}, form='S/Ü 90')),
            M('BM-XX-CH-02', 'Chor', 4, C('Hochschulchor/Kammerchor', {5: 2, 6: 2}, 'Hochschulchor', 'G 120')),
            M('BM-OI-BP-IV', 'Bachelorprojekt bei IV', 12,
              C('Bachelorprojekt', {7: 6, 8: 6}, 'Bachelorprojekt: Kolloquium', 'S'),
              X('Rezital (50 Min.)', 8, 40), X('Wissenschaftliche Arbeit (20 Seiten)', 8, 5), weight=45, final=True),
            weight=1),
          weight=65 if strings else 70, note='Wahl zu Beginn des 5. Semesters.'),
        M('BM-OI-WK', 'Wahlkatalog', 10,
          *[L(f'BM-OI-WK-{n:02d}', title, cp, course, form, EARLY if title == 'Historisches Instrument' else None) for n, (title, cp, course, form) in enumerate(
              [('Kammermusik', 3, 'Kammermusik', 'E+x 45')] * 2
              + [('Kammerorchester', 2, 'Kammerorchester', 'G 90')] * 2
              + [('Historische Spielpraxis auf modernen Instrumenten', 2, 'Historische Spielpraxis auf modernen Instrumenten', 'G 90')] * 2
              + [('Historisches Instrument', 3, 'Historisches Instrument', 'E 60')] * 2
              + ([] if strings else [('Liszt Brass Ensemble', 2, 'Liszt Brass Ensemble', 'G 180')] * 2), 1)],
          note='Frei aus den Angeboten der Institute oder dem Allgemeinen Wahlkatalog; unbenotet.'),
    ]


VOICE = [
    M('BM-GM-01', 'Gesang/Repertoire I', 51,
      C('Gesang', {1: 9, 2: 9, 3: 9, 4: 9}, form='E 120', taught=HF),
      C('Werkstudium Vokalmusik', {2: 1, 3: 1, 4: 1}, form='E 45', taught=WS),
      C('Repertoirekunde Musiktheater', {2: 2}, form='G 90'),
      C('Repertoirekunde Lied', {3: 2}, form='G 90'),
      C('Italienisch', {1: 4, 2: 4}, 'Italienisch für Sänger:innen {n}', 'G 180'),
      X('Klausur (90 Min.) und mündliche Prüfung (30 Min.) Italienisch', 2, 0),
      X('Künstlerisch-praktische Prüfung (20 Min.)', 4, 0)),
    M('BM-GM-02', 'Gesang/Repertoire II', 52,
      C('Gesang', {5: 9, 6: 9, 7: 9, 8: 9}, form='E 120', taught=HF),
      C('Werkstudium Vokalmusik', {5: 2, 6: 3, 7: 2, 8: 3}, form='E 60', taught=WS),
      C('Ensemblesingen/Vom-Blatt-Singen', {5: 1, 6: 1, 7: 1, 8: 1}, form='G 30'),
      C('Phonetik gesungener Sprachen', {5: 1, 6: 1}, form='G 45'),
      note='Voraussetzung: 100 CP und bestandenes Modul Gesang/Repertoire I.'),
    M('BM-GM-03', 'Bühne I', 21,
      C('Körpererfahrung/Bewegung/Tanz', {1: 1, 2: 1, 3: 1, 4: 1}, 'Körpererfahrung/Bewegung/Tanz {n}', 'G 90'),
      C('Grundlagen Schauspiel/Improvisation', {1: 3, 2: 3, 3: 3}, 'Grundlagen Schauspiel/Improvisation {n}', 'G 180'),
      C('Grundlagen Bühnensprechen', {1: 2, 2: 2}, 'Grundlagen Bühnensprechen {n}', 'G 120'),
      C('Bühnensprechen', {3: 2, 4: 2}, form='E 60', taught=SPEECH)),
    M('BM-GM-04', 'Bühne II', 20,
      C('Körpererfahrung/Bewegung/Tanz', {5: 1, 6: 1, 7: 1, 8: 1}, 'Körpererfahrung/Bewegung/Tanz {n}', 'G 90', start=5),
      C('Bühnensprechen', {5: 2, 6: 2, 7: 2, 8: 2}, form='E 60', taught=SPEECH),
      C('Opernpartien szenisch', {6: 3, 7: 3}, form='E+x 90'),
      C('Opernpartien – musikalische Einstudierung', {5: 1, 7: 1}, form='E+x 30'),
      X('Künstlerisch-praktische Prüfung Bühnensprechen (15 Min.)', 8, 0)),
    M('BM-GM-05', 'Szenische Aufführung Musiktheater I', 8,
      C('Musikalische Einstudierung', {3: 2}, 'Szenische Produktion: musikalische Einstudierung', 'E 30, G 60'),
      C('Szenische Erarbeitung', {4: 6}, 'Szenische Produktion', 'E/G 360'),
      X('Künstlerisch-praktische Prüfung (20 Min.)', 4, 0)),
    M('BM-GM-06', 'Szenische Aufführung Musiktheater II', 8,
      C('Musikalische Einstudierung', {7: 2}, 'Szenische Produktion: musikalische Einstudierung', 'E 30, G 60'),
      C('Szenische Erarbeitung', {8: 6}, 'Szenische Produktion', 'E/G 360'),
      X('Künstlerisch-praktische Prüfung (20 Min.)', 8, 1), weight=35),
    M('BM-GM-BP-KV', 'Bachelorprojekt', 12,
      C('Bachelorprojekt', {7: 6, 8: 6}, 'Bachelorprojekt: Kolloquium', 'S'),
      X('Rezital (40 Min.)', 8, 33), X('Programmheft oder Dokumentation (8–10 Seiten)', 8, 2), weight=35),
    M('BM-GM-07', 'Berufsvorbereitung', 6,
      C('Grundlagen Phonetik – Internationales Phonetisches Alphabet (IPA)', {5: 1}, form='G 30'),
      C('Aufführungspraxis des Rezitativs', {6: 2}, form='G 90'),
      C('Liedgestaltung (Liedduo)', {7: 3}, form='E+x 45'),
      X('Künstlerisch-praktische Prüfung Liedgestaltung (20 Min.)', 7), weight=10),
    M('BM-XX-KL-04', 'Klavier I', 4, C('Klavier', {1: 2, 2: 2}, form='E 30', taught=PIANO)),
    M('BM-XX-KL-05', 'Klavier II', 4, C('Klavier', {3: 2, 4: 2}, form='E 30', taught=PIANO), X('Künstlerisch-praktische Prüfung (15 Min.)', 4, 0)),
    *theory(5),
    M('BM-XX-BF-P1', 'Professionalisierung I – Berufsfeldbezogene Grundlagen', 6,
      C('Einführung Berufspraxis', {5: 1}, form='G 90'),
      C('Anatomie/Physiologie der Stimme', {5: 3}, form='G 90'),
      G('BM-XX-BF-P1-W', 'Wahl aus dem Angebot', 2, *professional_basics([5, 6, 5, 4, 6, 4, 6]))),
    scientific_basics(),
    M('BM-XX-WK', 'Wahlkatalog', 11,
      *[L(f'BM-XX-WK-{n:02d}', title, cp, course, form) for n, (title, cp, course, form) in enumerate([
          ('Konzert- und Theaterpraxis', 6, 'Konzert- und Theaterpraxis', 'G 180'),
          ('Aufführungspraxis des Rezitativs', 2, 'Aufführungspraxis des Rezitativs', 'G 90'),
          ('Liedgestaltung (Liedduo)', 3, 'Liedgestaltung (Liedduo)', 'E+x 45'), ('Liedgestaltung (Liedduo)', 3, 'Liedgestaltung (Liedduo)', 'E+x 45'),
          ('Liedgestaltung (Liedkurs)', 2, 'Liedgestaltung (Liedkurs)', 'G 90'), ('Liedgestaltung (Liedkurs)', 2, 'Liedgestaltung (Liedkurs)', 'G 90'),
          ('Gesangspädagogik – Grundlagen', 13, 'Fachdidaktik Gesang/Grundlagen', 'G 90'),
      ], 1)],
      note='Frei aus den Angeboten der Institute oder dem Allgemeinen Wahlkatalog; unbenotet.'),
]

PIANO_PLAN = [
    M('BM-KL-01', 'Künstlerische Präsentation I', 50,
      C('Klavier', {1: 15, 2: 12, 3: 13, 4: 10}, form='E 90', taught=HF),
      X('Künstlerisch-praktische Prüfung (30 Min.)', 4, 0)),
    M('BM-KL-02', 'Künstlerische Präsentation II', 47, C('Klavier', {5: 11, 6: 12, 7: 12, 8: 12}, form='E 90', taught=HF),
      note='Voraussetzung: 100 CP und bestandene Künstlerische Präsentation I.'),
    M('BM-KL-03', 'Instrumentale Berufsvorbereitung I', 22,
      C('Kammermusik/Ensemble', {3: 5, 4: 5}, 'Kammermusik', 'E+x 45'),
      C('Künstlerische Liedgestaltung', {2: 3, 3: 3, 4: 3}, form='E+x 45'),
      C('Literaturkunde/Blattspiel', {1: 1, 2: 1, 3: 1}, form='G 30'),
      X('Künstlerisch-praktische Prüfung Blattspiel (10 Min.)', 3, 0),
      X('Künstlerisch-praktische Prüfung Liedgestaltung (20 Min.)', 4, 1), weight=5),
    M('BM-KL-04', 'Instrumentale Berufsvorbereitung II', 20,
      C('Klavier im 20./21. Jahrhundert (Grundkurs)', {5: 1}, form='G 60'),
      C('Kammermusik/Ensemble', {5: 5, 6: 5, 7: 5}, 'Kammermusik', 'E+x 45'),
      C('Improvisation', {5: 2, 6: 2}, 'Instrumentale Improvisation: Neue Musik', 'E+x 60'),
      X('Künstlerisch-praktische Prüfung Kammermusik (10 Min.)', 7, 1), weight=10),
    M('BM-XX-CH-02', 'Chor', 4, C('Hochschulchor/Kammerchor', {1: 2, 2: 2}, 'Hochschulchor', 'G 120')),
    *theory(6.25),
    M('BM-KL-MP-01', 'Musikpädagogik I.1', 4,
      C('Musikpädagogik 1', {2: 2}, form='V 90'),
      C('Fachdidaktik 1 und 2', {1: 1, 2: 1}, 'Fachdidaktik Klavier {n}', 'S 90'),
      X('Mündliche Prüfung Musikpädagogik 1 (20 Min.)', 2, 0)),
    M('BM-KL-MP-02', 'Musikpädagogik I.2', 3,
      C('Fachdidaktik 3 und 4', {3: 1, 4: 1}, 'Fachdidaktik Klavier {n}', 'S 90', start=3),
      C('Lehrpraxis/Hospitation/Pädagogisches Projekt', {4: 1}, 'Lehrpraxis/Hospitation', 'Ü 45')),
    M('BM-XX-BF-P1', 'Professionalisierung I – Berufsfeldbezogene Grundlagen', 5, *professional_basics([1, 2, 1, 3, 1, 2, 1])),
    scientific_basics(),
    G('V', 'Vertiefung', 38,
      G('KV', 'Künstlerische Vertiefung', 38,
        M('BM-KL-KV', 'Künstlerische Vertiefung', 26,
          C('Künstlerische Projektarbeit/Rezitals', {5: 6, 6: 6, 7: 6, 8: 6}, form='E'),
          C('Einführung in die Korrepetitionspraxis', {6: 1, 7: 1}, form='E+x 45')),
        M('BM-KL-BP-KV', 'Bachelorprojekt bei KV', 12,
          C('Bachelorprojekt', {7: 6, 8: 6}, 'Bachelorprojekt: Kolloquium', 'S'),
          X('Rezital (75 Min.)', 8, 58), X('Programmheft oder Dokumentation (8–10 Seiten)', 8, 2), weight=60),
        weight=1),
      G('IV', 'Instrumentalpädagogische Vertiefung', 38,
        *pedagogy_deepening(
            'BM-KL-IV', ' Klavier',
            [C('Fachdidaktik 5', {5: 1}, 'Fachdidaktik Klavier 5', 'S 90'), C('Lehrpraxis', {5: 3, 6: 3}, 'Lehrpraxis Klavier', 'Ü 60')],
            [C('Fachdidaktik 6', {8: 2}, 'Fachdidaktik Klavier 6', 'S 60'), C('Lehrpraxis', {7: 3, 8: 3}, 'Lehrpraxis Klavier', 'Ü 60')],
            [X('Mündliche Prüfung Fachdidaktik (30 Min.)', 8, 2), X('Lehrprobe mit Gespräch (30 + 10 Min.)', 8), X('Lehrprobe mit Gespräch (45 + 10 Min.)', 8)],
            weight_fd2=20),
        M('BM-XX-IV-02', 'EMP/Rhythmik', 5, C('Rhythmik', {6: 2}, form='G 60'), C('Elementare Musikpädagogik', {7: 3}, form='S/Ü 90')),
        M('BM-KL-BP-IV', 'Bachelorprojekt bei IV', 12,
          C('Bachelorprojekt', {7: 6, 8: 6}, 'Bachelorprojekt: Kolloquium', 'S'),
          X('Rezital (55 Min.)', 8, 30), X('Wissenschaftliche Arbeit (20 Seiten)', 8, 5), weight=35),
        weight=1),
      weight=60, note='Wahl zu Beginn des 5. Semesters.'),
    M('BM-KL-WK', 'Wahlkatalog', 10,
      *[L(f'BM-KL-WK-{n:02d}', title, cp, course, form) for n, (title, cp, course, form) in enumerate([
          ('Kammermusik', 5, 'Kammermusik', 'E+x 45'), ('Kammermusik', 5, 'Kammermusik', 'E+x 45'),
          ('Künstlerische Liedgestaltung', 3, 'Künstlerische Liedgestaltung', 'E+x 45'),
          ('Liedkurse', 2, 'Liedgestaltung (Liedkurs)', 'G 90'), ('Liedkurse', 2, 'Liedgestaltung (Liedkurs)', 'G 90'),
          ('Historische Tasteninstrumente', 2, 'Historische Tasteninstrumente', 'G 30'),
          ('Klavierauszugspiel/Blattspiel', 1, 'Klavierauszugspiel/Blattspiel', 'E+x 45'),
          ('Improvisation (jazzorientiert oder musikschulorientiert)', 2, 'Improvisation (jazzorientiert oder musikschulorientiert)', 'E 60'),
          ('Geschichte des Klavierliedes', 3, 'Geschichte des Klavierliedes', 'G 90'),
      ], 1)],
      note='Frei aus den Angeboten der Institute oder dem Allgemeinen Wahlkatalog; unbenotet.'),
]

PROFILE = [
    *[(f'MM-XX-OEP-0{n}', f'Orchester- und Ensemblepraxis {r}', 5, 'Hochschulorchester', 'G 180, E 30') for n, r in enumerate(['I', 'II', 'III', 'IV', 'V', 'VI'], 1)],
    *[(f'MM-XX-KAM-0{n}', f'Kammermusik {r}', 5, 'Kammermusik', 'E+x 45') for n, r in enumerate(['I', 'II', 'III', 'IV'], 1)],
    *[(f'MM-XX-AUF-0{n}', f'Aufführungspraxis {r}', 5, 'Ensemble für Alte Musik', 'G') for n, r in enumerate(['I', 'II', 'III'], 1)],
    *[(f'MM-XX-BK-0{n}', f'Beruf/Karriere {r}', 5, 'Auftrittstraining', 'G') for n, r in enumerate(['I', 'II'], 1)],
    *[(f'MM-XX-DIG-0{n}', f'Digitale Praktiken {r}', 5, 'Tonstudiotechnik & Akustik', 'S') for n, r in enumerate(['I', 'II'], 1)],
    *[(f'MM-XX-KUMA-0{n}', f'Musik- und Kulturmanagement {r}', 5, 'Einführung in die Musikvermittlung', 'S') for n, r in enumerate(['I', 'II'], 1)],
    *[(f'MM-XX-MPGR-0{n}', f'Musikpädagogik Grundlagen {r}', 5, 'Musikpädagogik 1', 'V 90, S 90, Ü 60') for n, r in enumerate(['I', 'II'], 1)],
    ('MM-XX-MW-01', 'Musikwissenschaft I', 10, 'Notationsgeschichte II', 'S 90'),
    ('MM-XX-MT-01', 'Ergänzungsmodul Musiktheorie I', 5, 'Historische Satzlehre 1', 'S/Ü 90'),
]

MASTER = [
    G('', 'Kernbereich – Pflichtmodule', 70, *[G('', f'{n}. Semester', cp,
        M(f'MM-ST-A{n}', f'Streichinstrumente A {r}', cp,
          C('Hauptfach', {n: cp - 2}, form='E 90', taught=HF), C('Werkstudium', {n: 2}, form='E 60', taught=WS)),
        M(f'MM-ST-B{n}', f'Streichinstrumente B {r}', cp,
          C('Hauptfach', {n: cp - 7}, form='E 60', taught=HF), C('Werkstudium Hauptfach', {n: 2}, form='E 45', taught=WS),
          C('Zweitinstrument (Violine/Viola)', {n: 5}, form='E 30')),
        M(f'MM-ST-C{n}', f'Streichinstrumente C {r}', cp,
          C('Hauptfach', {n: cp - 7}, form='E 60', taught=HF), C('Werkstudium Hauptfach', {n: 2}, form='E 45', taught=WS),
          C('Historisches Instrument', {n: 5}, form='E 30', taught=EARLY)))
      for n, r, cp in [(1, 'I', 20), (2, 'II', 20), (3, 'III', 15), (4, 'IV', 15)]],
      note='Variante A, B oder C, in jedem Semester neu wählbar. B nur für Violine und Viola. Unbenotet.'),
    M('MM-PB', 'Profilierungsbereich', 30, *[L(code, title, cp, course, form) for code, title, cp, course, form in PROFILE],
      note='Wahlpflichtmodule im Umfang von 30 CP, empfohlen 10/10/5/5 je Semester. Ab 20 CP passender Module steht ein Schwerpunkt im Zeugnis.'),
    M('MM-XX-MPRO', 'Masterprojekt', 20,
      C('Masterprojekt', {3: 10, 4: 10}, 'Masterprojekt: Kolloquium', 'S'),
      X('Künstlerische Präsentation (ca. 50 Min.)', 4, 85), X('Schriftlicher Teil (15–20 Seiten)', 4, 15), weight=100, final=True,
      note='Voraussetzung: Kernbereich I und II bestanden. Die Note des Masterprojekts ist die Gesamtnote.'),
]

# Lehramt Musik an Gymnasien, jointly with the Friedrich-Schiller-Universität Jena: B.Ed. (FPSO 2025), M.Ed. (FPSO 2026),
# Erste Staatsprüfung (FPSO 2025). Module codes and credits follow the FPSO; the spread over semesters is approximated.
JENA = 'jena'  # taught at the FSU Jena and credited here


def schooling(prefix):
    """Modules the B.Ed. and the Staatsexamen share, as {code: module}."""
    return {
        'CE-01': M(f'{prefix}-CE-01', 'Chor- und Ensembleleitung I', 7,
                   C('Chorleitung Pop/Jazz', {1: 2, 2: 2}, form='G 90'), C('Grundlagen Dirigieren', {1: 1, 2: 1}, form='G 45'),
                   C('Hochschulchor', {1: 1}, form='G 135'), X('Praktische Prüfung Chorleitung (15 Min.)', 2), weight=7),
        'CE-02': M(f'{prefix}-CE-02', 'Chor- und Ensembleleitung II', 5,
                   C('Chorleitung Klassik', {3: 2, 4: 1}, form='G 90'), C('Grundlagen Bandleitung', {4: 2}, form='G 90'),
                   X('Praktische Prüfung Ensembleleitung (15 Min.)', 4), weight=5),
        'MuP-01': M(f'{prefix}-MuP-01', 'Musik und Performance', 4, C('Musik und Performance', {4: 4}, form='S 90'), X('Präsentation', 4), weight=4),
        'MT-01': M(f'{prefix}-MT-01', 'Musiktheorie I', 4,
                   C('Allgemeine Musiklehre', {1: 2}, form='Ü 60'), C('Harmonielehre 1', {1: 2}, 'Gehörbildung 1/Harmonielehre 1', 'G 60'),
                   X('Klausur (90 Min.)', 1), weight=4),
        'MT-02': M(f'{prefix}-MT-02', 'Musiktheorie II', 4,
                   C('Harmonielehre und Gehörbildung 2', {2: 2, 3: 2}, 'Gehörbildung {n}/Harmonielehre {n}', 'G 60', start=2),
                   X('Klausur (180 Min.)', 3), weight=4),
        'MW-01': M(f'{prefix}-MW-01', 'Musikwissenschaft I', 6,
                   C('Musikgeschichte im Überblick', {1: 3, 2: 3}, {'WiSe': 'Musikgeschichte im Überblick II', 'SoSe': 'Musikgeschichte im Überblick I'}, 'V 90'),
                   X('Mündliche Prüfung (20 Min.)', 2), weight=6),
        'MW-02': M(f'{prefix}-MW-02', 'Musikwissenschaft II', 4,
                   C('Einführung in das wissenschaftliche Arbeiten', {3: 4}, 'Einführung in das wissenschaftliche Arbeiten und Schreiben', 'S 90'),
                   X('Hausarbeit', 3), weight=4),
        'MP-01': M(f'{prefix}-MP-01', 'Musikpädagogik I', 6, C('Musikpädagogik 1', {1: 3, 2: 3}, form='S 90'), X('Referat mit Ausarbeitung', 2), weight=6),
        'MP-02': M(f'{prefix}-MP-02', 'Musikpädagogik II', 4, C('Fachdidaktik Musik', {3: 2, 4: 2}, form='S 90'), X('Hausarbeit', 4), weight=4),
        'FD-01': M(f'{prefix}-FD-01', 'Fachdidaktik', 5, C('Begleitseminar Praxissemester', {5: 5}, form='S 45'), X('Praktikumsbericht', 5), weight=5),
    }


BED_SHARED = schooling('BE')
BED = [
    M('BE-BP-01', 'Berufsfeldorientierte künstlerische Praxis I', 17,
      C('Künstlerisches Schwerpunktfach', {1: 4, 2: 4}, form='E 60', taught=HF), C('Gesang/Stimmbildung', {1: 2, 2: 2}, 'Stimmbildung', 'E 30', VOCAL),
      C('Schulpraktisches Klavierspiel', {1: 2, 2: 2}, form='E 30', taught=PIANO), C('Instrumentenpool', {1: 1}, form='G 60'),
      X('Künstlerisch-praktische Prüfung (20 Min.)', 2), weight=17),
    M('BE-BP-02', 'Berufsfeldorientierte künstlerische Praxis II', 27,
      C('Künstlerisches Schwerpunktfach', {3: 6, 4: 6}, form='E 60', taught=HF), C('Schulpraktisches Klavierspiel', {3: 3, 4: 3}, form='E 30', taught=PIANO),
      C('Stimmphysiologie', {3: 2}, form='V 90'), C('Schulpraktisches Gruppenmusizieren', {3: 3, 4: 4}, form='G 90'),
      X('Künstlerisch-praktische Prüfung (30 Min.)', 4), weight=27),
    M('BE-BP-03', 'Berufsfeldorientierte künstlerische Praxis III', 6,
      C('Künstlerisches Schwerpunktfach', {6: 6}, form='E 60', taught=HF), X('Künstlerische Präsentation (30 Min.)', 6), weight=6),
    BED_SHARED['CE-01'], BED_SHARED['CE-02'], BED_SHARED['MuP-01'], BED_SHARED['MP-01'], BED_SHARED['MP-02'],
    M('BE-IGP-01', 'Instrumental- und Gesangspädagogik I', 7, C('Musikpädagogik 3 (Instrumental- und Gesangspädagogik)', {3: 3, 4: 4}, form='S 90'), X('Lehrprobe', 4), weight=7),
    M('BE-IGP-02', 'Instrumental- und Gesangspädagogik II', 8, C('Lehrpraxis', {6: 8}, 'Lehrpraxis 1', 'E 45'), X('Lehrprobe mit Kolloquium', 6), weight=8),
    BED_SHARED['MT-01'], BED_SHARED['MT-02'], BED_SHARED['MW-01'], BED_SHARED['MW-02'],
    M('BE-MG-01', 'Musik und Gesundheit', 6,
      C('Musik und Gesundheit 1', {1: 2}, form='V+Ü 90'), C('Musik und Gesundheit 2', {2: 2}, form='V+Ü 90'), C('Körpertechniken', {3: 2}, 'Alexander-Technik', 'G')),
    G('', 'Bildungswissenschaften (FSU Jena)', 38,
      L('L1GR', 'Bildungswissenschaften 1', 10, JENA), L('L2G', 'Bildungswissenschaften 2', 5, JENA),
      L('L3GR', 'Schulpraktische Studien (Praxissemester)', 10, JENA), L('RSP', 'Reflexion schulischer Praxis', 10, JENA),
      L('L4G', 'Bildungswissenschaften 4', 3, JENA),
      note='An der Friedrich-Schiller-Universität Jena, dort als Nebenhörer:in; wird angerechnet. Praxissemester im 5. Semester (Jenaer Modell).'),
    BED_SHARED['FD-01'],
    M('BE-WB-01', 'Wahlbereich', 12, *[L(f'BE-WB-W{n}', title, cp, course, form) for n, (title, cp, course, form) in enumerate([
        ('Kammermusik', 4, 'Kammermusik', 'E+x 45'), ('Unterrichtspraktikum', 4, 'Lehrpraxis 2', 'E 45'),
        ('Rhythmik', 4, 'Rhythmik', 'G 90'), ('Tonstudiotechnik', 4, 'Tonstudiotechnik & Akustik', 'S'),
        ('Elementare Musikpädagogik', 4, 'Elementare Musikpädagogik', 'S 90'), ('Hochschulorchester', 4, 'Hochschulorchester', 'G 180'),
    ], 1)], note='Mit Kammermusik, Unterrichtspraktikum und Lehrpraxis 2 bestätigt das Zeugnis zusätzlich die Qualifikation als Instrumental- oder Gesangspädagog:in.'),
    M('BE-BA', 'Bachelorarbeit', 10, C('Bachelorarbeit', {6: 10}, 'Bachelorprojekt: Kolloquium', 'S'), X('Bachelorarbeit (30–40 Seiten)', 6), weight=30, final=True,
      note='Anmeldung ab 120 CP; Thema aus Musikwissenschaft oder Musikpädagogik. Zählt dreifach.'),
]

MED_PROFILES = [  # code, Profil, (part, credits, course)
    ('ME-PR-01', 'Instrumental- und Gesangspädagogik', [('Fachdidaktik Instrumental- und Gesangspädagogik', 14, 'Fachdidaktik'), ('Lehrpraxis', 16, 'Lehrpraxis 2')]),
    ('ME-PR-02', 'Musikpädagogik', [('Musikpädagogik I', 10, 'Musikpädagogik 1'), ('Musikpädagogik II', 5, 'Fachdidaktik Musik'), ('Forschungsprojekt', 15, 'Musikpädagogik: Forschungskolloquium')]),
    ('ME-PR-03', 'Schulpraktisches Klavierspiel', [('Schulpraktisches Klavierspiel I', 11, 'Unterrichtspraktisches Klavierspiel'), ('Schulpraktisches Klavierspiel II', 19, 'Unterrichtspraktisches Klavierspiel')]),
    ('ME-PR-04', 'Chor- oder Ensembleleitung klassisch', [('Chor- oder Ensembleleitung I', 16, 'Chorleitung Klassik'), ('Chor- oder Ensembleleitung II', 14, 'Chorleitung Klassik')]),
    ('ME-PR-05', 'Chor- und Ensembleleitung Pop/Jazz', [('Chor- und Ensembleleitung Pop/Jazz I', 10, 'Chorleitung Pop/Jazz'), ('Chor- und Ensembleleitung Pop/Jazz II', 20, 'Grundlagen Bandleitung')]),
    ('ME-PR-06', 'Kirchenmusik', [('Kirchenmusik I', 19, 'Liturgisches Orgelspiel'), ('Kirchenmusik II', 11, 'Liturgik und Hymnologie')]),
    ('ME-PR-07', 'EMP/Rhythmik', [('Elementare Musikpädagogik', 10, 'Elementare Musikpädagogik'), ('Rhythmik', 13, 'Rhythmik'), ('Lehrpraxis EMP/Rhythmik', 7, 'Lehrpraxis 2')]),
    ('ME-PR-08', 'Musiktheorie', [('Musiktheorie Profil I', 20, 'Historische Satzlehre 1'), ('Musiktheorie Profil II', 10, 'Werkanalyse 2')]),
]
MED = [
    M('ME-BP-01', 'Berufsfeldorientierte künstlerische Praxis', 10,
      C('Künstlerisches Schwerpunktfach', {1: 3, 2: 3}, form='E 60', taught=HF), C('Schulpraktisches Klavierspiel', {1: 2, 2: 2}, form='E 30', taught=PIANO),
      X('Künstlerisch-praktische Prüfung (20 Min.)', 2), weight=10),
    M('ME-CE-01', 'Ensembleleitung', 9, C('Ensembleleitung', {1: 3, 2: 3, 3: 3}, 'Grundlagen Bandleitung', 'G 90'), X('Praktische Prüfung Ensembleleitung (20 Min.)', 3),
      weight=9, note='Schwerpunkt Instrumentale Ensembleleitung Klassik oder Bandleitung.'),
    M('ME-MP-01', 'Musikpädagogik III', 9, C('Musikpädagogik III', {1: 3, 2: 3, 3: 3}, 'Musikpädagogik 3 (Instrumental- und Gesangspädagogik)', 'S 90'), X('Hausarbeit', 3), weight=9),
    M('ME-MT-03', 'Musiktheorie III', 6, C('Werkanalyse', {1: 3, 2: 3}, 'Werkanalyse 2', 'G 60'), X('Klausur (180 Min.)', 2), weight=6),
    M('ME-MT-04', 'Musiktheorie IV', 4, C('Satztechniken des 20./21. Jahrhunderts', {3: 4}, 'Satztechniken des 20./21. Jh. 1', 'G 60'), X('Hausarbeit', 3), weight=4),
    M('ME-W-01', 'Wissenschaftsbereich', 6, C('Spezialvorlesung Musikwissenschaft', {1: 3, 2: 3}, '„Pour la fin du temps“ – Jüdisch-christliche Endzeitvorstellungen in der Musik', 'V 90'), weight=0),
    G('', 'Bildungswissenschaften (FSU Jena)', 10, L('L6G', 'Bildungswissenschaften 6', 5, JENA), L('L7G', 'Bildungswissenschaften 7', 5, JENA)),
    G('', 'Profil', 30, *[M(code, title, 30, *[C(part, {2 + k: cp}, course, 'S 90') for k, (part, cp, course) in enumerate(parts)]) for code, title, parts in MED_PROFILES],
      note='Ein Profil im Umfang von 30 CP; es steht im Zeugnis.'),
    M('ME-WM', 'Wahlmodul', 16, *[L(f'ME-WM-0{n}', title, cp, course, form) for n, (title, cp, course, form) in enumerate([
        ('Kammermusik', 8, 'Kammermusik', 'E+x 45'), ('Hochschulorchester', 8, 'Hochschulorchester', 'G 180'),
        ('Tonstudiotechnik', 8, 'Tonstudiotechnik & Akustik', 'S'), ('Musikvermittlung', 8, 'Einführung in die Musikvermittlung', 'S'),
    ], 1)]),
    M('ME-MA-00', 'Masterarbeit', 20, C('Masterarbeit', {3: 10, 4: 10}, 'Masterprojekt: Kolloquium', 'S'), X('Masterarbeit (60–80 Seiten)', 4), weight=80, final=True,
      note='Anmeldung ab 80 CP. Zählt vierfach.'),
]

STEX_SHARED = schooling('ZF')
STEX = [
    M('ZF-BP-01', 'Berufsfeldorientierte künstlerische Praxis I', 14,
      C('Künstlerisches Schwerpunktfach', {1: 4, 2: 4}, form='E 60', taught=HF), C('Gesang/Stimmbildung', {1: 2, 2: 2}, 'Stimmbildung', 'E 30', VOCAL),
      C('Schulpraktisches Klavierspiel', {1: 1, 2: 1}, form='E 30', taught=PIANO), X('Künstlerisch-praktische Prüfung (20 Min.)', 2), weight=1),
    M('ZF-BP-02', 'Berufsfeldorientierte künstlerische Praxis II', 20,
      C('Künstlerisches Schwerpunktfach', {3: 5, 4: 5}, form='E 60', taught=HF), C('Schulpraktisches Klavierspiel', {3: 2, 4: 2}, form='E 30', taught=PIANO),
      C('Schulpraktisches Gruppenmusizieren', {3: 3, 4: 3}, form='G 90'), X('Künstlerisch-praktische Prüfung (30 Min.)', 4), weight=1),
    M('ZF-BP-03', 'Berufsfeldorientierte künstlerische Praxis III', 14,
      C('Künstlerisches Schwerpunktfach', {6: 4, 7: 4}, form='E 60', taught=HF), C('Schulpraktisches Klavierspiel', {6: 3, 7: 3}, form='E 30', taught=PIANO),
      X('Künstlerisch-praktische Prüfung (60 Min.)', 7), weight=6, note='Zählt 40 % der Fachendnote Musik.'),
    STEX_SHARED['CE-01'], STEX_SHARED['CE-02'], STEX_SHARED['MuP-01'], STEX_SHARED['MP-01'], STEX_SHARED['MP-02'],
    M('ZF-MP-03', 'Musikpädagogik III', 7, C('Musikpädagogik III', {6: 3, 7: 4}, 'Musikpädagogik 3 (Instrumental- und Gesangspädagogik)', 'S 90'), X('Hausarbeit', 7), weight=0),
    STEX_SHARED['MT-01'], STEX_SHARED['MT-02'],
    M('ZF-MT-03', 'Musiktheorie III', 3, C('Werkanalyse', {6: 3}, 'Werkanalyse 2', 'G 60'), X('Mündliche Prüfung (15 Min.)', 6), weight=1),
    STEX_SHARED['MW-01'], STEX_SHARED['MW-02'], STEX_SHARED['FD-01'],
    M('ZF-WB-01', 'Musikpraxis und musikalische Bildung', 5, *[L(f'ZF-WB-W{n}', title, 5, course, form) for n, (title, course, form) in enumerate([
        ('Kammermusik', 'Kammermusik', 'E+x 45'), ('Hochschulorchester', 'Hochschulorchester', 'G 180'), ('Rhythmik', 'Rhythmik', 'G 90'),
    ], 1)]),
    G('', 'Zweites Fach, Bildungswissenschaften und Praxissemester (FSU Jena)', 168,
      L('L1GR', 'Bildungswissenschaften 1', 10, JENA), L('L3GR', 'Schulpraktische Studien (Praxissemester)', 30, JENA),
      L('ZF2', 'Zweites Prüfungsfach', 90, JENA), L('BW', 'Bildungswissenschaften', 10, JENA), L('StP2', 'Staatsprüfungsmodule Zweitfach und Bildungswissenschaften', 28, JENA)),
    M('ZF-StPrA', 'Wissenschaftliche Hausarbeit', 20, C('Wissenschaftliche Hausarbeit', {9: 10, 10: 10}, 'Masterprojekt: Kolloquium', 'S'),
      X('Wissenschaftliche Hausarbeit (60–80 Seiten)', 10), weight=6, final=True, note='Die Erste Staatsprüfung nimmt das Landesprüfungsamt ab (ThürEStPLGymVO).'),
]

# Kirchenmusik A (Diplom, FPSO 2012): module credits and final weights from the FPSO, the spread over semesters approximated.
CHURCH_CONFESSIONS = [
    ('KM-IV-EV', 'Evangelische Kirchenmusik', 'Hymnologie', 'Hymnologie (mündlich)'),
    ('KM-IV-KA', 'Katholische Kirchenmusik', 'Gregorianik und Scholaleitung', 'Gregorianik (mündlich)'),
]
CHURCH = [
    M('KM-I', 'Künstlerische Präsentation und Instrumentalbereich', 88,
      C('Orgel-Literaturspiel', {s: 6 if s < 10 else 3 for s in range(1, 11)}, form='E 90', taught=HF),
      C('Klavier', {s: 3 for s in range(1, 9)}, form='E 45', taught=PIANO), C('Generalbass', {3: 1, 4: 2}, 'Historische Tasteninstrumente', 'G 30'),
      C('Orgelexkursionen', {2: 2, 6: 2}, 'Orgelexkursion', 'P'),
      X('Orgel-Literaturspiel (60–70 Min.)', 10, 5), X('Klavier', 8, 2), weight=6),
    M('KM-II', 'Vokaler Bereich und Dirigieren', 61,
      C('Chorleitung', {s: 3 for s in range(3, 10)}, form='E 60'), C('Orchesterleitung', {s: 2 for s in range(5, 11)}, form='E 45'),
      C('Gesang', {s: 2 for s in range(1, 5)}, 'Stimmbildung', 'E 30'), C('Partiturspiel', {3: 2, 4: 3}, 'Klavierauszugspiel/Blattspiel', 'E+x 45'),
      C('Musizieren mit Kindern', {5: 2, 6: 2}, 'Elementare Musikpädagogik', 'S 90'), C('Ensemblesingen', {1: 3, 2: 3, 3: 3, 4: 2}, 'Hochschulchor', 'G 135'),
      X('Chorleitung (45–60 Min.)', 10, 6), weight=6),
    M('KM-III', 'Improvisation', 60,
      C('Orgelimprovisation', {s: 4 if s < 10 else 5 for s in range(1, 11)}, form='E 60', taught=HF),
      C('Liturgisches Orgelspiel', {s: 2 for s in range(3, 11)}, form='E 45'), C('Jazz, Popularmusik und Band', {7: 3}, 'Instrumentale Improvisation', 'G 90'),
      X('Orgelimprovisation (30–40 Min.)', 10, 5), weight=4),
    G('', 'Kirchliche Fächer und Berufsfächer', 21, *[M(code, title, 21,
        C('Liturgik und Glaubenslehre', {3: 2, 4: 3}, 'Liturgik und Hymnologie', 'S 90'), C(subject, {5: 4, 6: 4}, form='S 90'),
        C('Praktika, Musikmanagement, Praxis des Berufslebens', {7: 4, 8: 4}, 'Einführung Berufspraxis', 'S'), X(exam, 9, 2))
      for code, title, subject, exam in CHURCH_CONFESSIONS], weight=2, note='Evangelische oder Katholische Kirchenmusik.'),
    M('KM-V', 'Musiktheorie', 22,
      C('Tonsatz', {s: 2 for s in range(1, 7)}, 'Historische Satzlehre 1', 'G 60'), C('Gehörbildung', {1: 2, 2: 2, 3: 2, 4: 2}, 'Gehörbildung {n}/Harmonielehre {n}', 'G 60'),
      C('Formenlehre', {5: 2}, form='Ü 90'), X('Klausur Tonsatz (240 Min.)', 6), weight=3),
    M('KM-VI', 'Musikgeschichte und Repertoire', 32,
      C('Musikgeschichte', {s: 2 for s in range(1, 9)}, {'WiSe': 'Musikgeschichte im Überblick II', 'SoSe': 'Musikgeschichte im Überblick I'}, 'V 90'),
      C('Orgelkunde', {5: 3, 6: 3}, 'Instrumentenkunde/Akustik', 'Ü 90'), C('Diplomarbeit', {10: 10}, 'Bachelorprojekt: Kolloquium', 'S'),
      X('Diplomarbeit', 10, 2, final=True), weight=3),
    M('KM-VII', 'Wahlobligatorischer Bereich', 16, *[L(f'KM-VII-0{n}', title, cp, course, form, VOCAL if course == 'Stimmbildung' else None) for n, (title, cp, course, form) in enumerate([
        ('Ergänzungsfach Cembalo', 4, 'Historische Tasteninstrumente', 'G 30'), ('Ergänzungsfach Klavier-Improvisation', 4, 'Improvisation', 'E 60'),
        ('Ergänzungsfach Jazz-Pop-Gesang', 4, 'Stimmbildung', 'E 30'), ('Ergänzungsfach Jazzchor-/Ensembleleitung', 4, 'Chorleitung Pop/Jazz', 'G 90'),
        ('Schwerpunktfach Chorleitung', 8, 'Chorleitung', 'E 60'), ('Schwerpunktfach Pädagogische Qualifikation', 8, 'Musikpädagogik 1', 'S 90'),
    ], 1)], note='Zwei Ergänzungsfächer zu je 4 CP und ein Schwerpunktfach zu 8 CP, ab dem 7. Semester.'),
]

CONCERT = [  # Künstlerisches Aufbaustudium Konzertexamen (Studien- und Prüfungsordnung 2007): no credits; bestanden or mit Auszeichnung
    M('KE-HF', 'Hauptfach', 0, C('Hauptfach', {1: 0, 2: 0, 3: 0, 4: 0}, form='E 90', taught=HF), X('Öffentliches Konzert', 2), X('Öffentliches Konzert', 3)),
    M('KE-KD', 'Konzertdiplom-Prüfung', 0, X('Rezital (ca. 70 Min.)', 4), X('Solokonzert mit Orchester', 4), final=True,
      note='Öffentlich. Bestanden mit zwei Dritteln, mit Auszeichnung mit drei Vierteln der Kommission.'),
]

DOCTORATE = [  # Promotionsordnung 2017; Dr. phil., summa cum laude bis rite
    M('PR-KOL', 'Doktorandenkolloquium', 0, C('Doktorandenkolloquium', {1: 0, 2: 0, 3: 0, 4: 0}, 'Doktorandenkolloquium', 'S'),
      X('Vortrag im Kolloquium', 2), X('Vortrag im Kolloquium', 4)),
    M('PR-DIS', 'Dissertation', 0, X('Dissertation', 6), final=True),
    M('PR-MP', 'Mündliche Prüfung', 0, X('Kolloquium oder Disputation (60–90 Min.)', 6), final=True),
]

OPERA = [  # Zertifikatsstudiengang Thüringer Opernstudio (FSO 2025), with DNT Weimar, Theater Erfurt, Altenburg-Gera and Nordhausen
    *[M(f'OS-KP-0{n}', f'Künstlerische Präsentation {r}', 30,
        C('Mitwirkung an Proben und Aufführungen', {n: 16}, 'Proben und Aufführungen am Partnertheater', 'P'),
        C('Hauptfach Gesang', {n: 9}, form='E 60', taught=HF), C('Werkstudium', {n: 4}, form='E 45', taught=WS),
        C('Auftritts- und Vorsingetraining', {n: 1}, 'Auftrittstraining', 'G'))
      for n, r in [(1, 'I'), (2, 'II')]],
]

STRINGS = ['Violine', 'Viola', 'Violoncello', 'Kontrabass', 'Harfe']
WINDS = ['Flöte', 'Oboe', 'Klarinette', 'Fagott', 'Horn', 'Trompete', 'Posaune', 'Tuba', 'Schlagwerk']
TEACHING = ['Klavier', 'Gesang', 'Jazz-Gesang', 'Violine', 'Violoncello', 'Flöte', 'Trompete', 'Schlagwerk']  # künstlerisches Schwerpunktfach im Lehramt
RESEARCH = ['Musikwissenschaft', 'Musikpädagogik', 'Kulturmanagement']  # Promotionsfächer

PROGRAMS = [  # name, degree, version, semesters, subjects, plan
    ('Streichinstrumente und Harfe', 'B.Mus.', 2017, 8, STRINGS, orchestral('SH')),
    ('Blasinstrumente und Schlagwerk', 'B.Mus.', 2017, 8, WINDS, orchestral('BS')),
    ('Gesang | Musiktheater', 'B.Mus.', 2017, 8, ['Gesang'], VOICE),
    ('Klavier', 'B.Mus.', 2017, 8, ['Klavier'], PIANO_PLAN),
    ('Streichinstrumente', 'M.Mus.', 2025, 4, STRINGS[:4], MASTER),
    ('Musik an Gymnasien (Doppelfach)', 'B.Ed.', 2025, 6, TEACHING, BED),
    ('Musik an Gymnasien (Doppelfach)', 'M.Ed.', 2026, 4, TEACHING, MED),
    ('Lehramt an Gymnasien, Studienfach Musik', 'Staatsexamen', 2025, 10, TEACHING, STEX),
    ('Kirchenmusik A', 'Diplom', 2012, 10, ['Orgel'], CHURCH),
    ('Konzertexamen', 'Konzertexamen', 2007, 4, STRINGS + WINDS + ['Gesang', 'Klavier', 'Orgel'], CONCERT),
    ('Promotion (Dr. phil.)', 'Dr. phil.', 2017, 6, RESEARCH, DOCTORATE),
    ('Thüringer Opernstudio', 'Zertifikat', 2025, 2, ['Gesang'], OPERA),
    ('Gasthörerschaft', 'Gasthörerschaft', 2007, 1, ['Musikwissenschaft'], []),
]
