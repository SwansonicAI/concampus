"""Documents as PDF, set from the templates that show them on screen: each `.document` of a rendered page becomes one, on
the Briefbogen chosen for its kind (Institution.papers, else DOCUMENTS). A Briefbogen (Layout, else STANDARD) places
its elements on the page in mm from the top left: the text area each page's text flows into, texts with placeholders,
the wordmark, pictures, areas and rules, a letter's address window and information block; and sets the text's type.
Only the markup the templates use is read (blocks(), inline()), every text is escaped, nothing is fetched; a design is
checked field by field before it is kept (clean()), uploads are tested and pictures drawn anew (accept()). ReportLab
sets the pages, in the open-licence fonts of core/static/core/fonts or the Hochschule's own."""
import re
import secrets
from collections import Counter
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont as Tools, newTable
from fontTools.varLib import instancer
from lxml import html as lxml_html
from PIL import Image, UnidentifiedImageError
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Flowable, Frame, KeepTogether, ListFlowable, ListItem, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from .models import Artwork, Institution, Layout

FONTS = Path(__file__).parent / 'static' / 'core' / 'fonts'
FAMILIES = {  # key: (name, file stem), the fonts that come with ConCampus, each in four faces
    'ptsans': ('PT Sans', 'PTSans'), 'lato': ('Lato', 'Lato'), 'fira': ('Fira Sans', 'FiraSans'),
    'ptserif': ('PT Serif', 'PTSerif'), 'plexserif': ('IBM Plex Serif', 'IBMPlexSerif'), 'crimson': ('Crimson Text', 'CrimsonText'),
}
FACES = {(400, False): 'Regular', (700, False): 'Bold', (400, True): 'Italic', (700, True): 'BoldItalic'}  # (weight, italic): file suffix
WEIGHTS = {100: 'Dünn', 200: 'Extraleicht', 300: 'Leicht', 400: 'Normal', 500: 'Mittel', 600: 'Halbfett', 700: 'Fett', 800: 'Extrafett', 900: 'Schwarz'}
OWN = 'eigen-'  # the key of a family the Hochschule uploaded: this and its name, slugified
WIDTH, HEIGHT = A4
PAGE = (210, 297)  # mm
INK, MUTED, LINE = HexColor('#000000'), HexColor('#5f5f5f'), HexColor('#d6d6d6')
SWATCHES = {'accent': None, 'ink': '#000000', 'muted': '#5f5f5f', 'line': '#d6d6d6', 'white': '#ffffff'}  # None: the Hochschule's
SKIP = {'form', 'button', 'script', 'style', 'input', 'select', 'textarea'}
GERMAN = {'page': 'Seite {} von {}', 'bank': 'Bankverbindung', 'copy': 'ABSCHRIFT'}  # what the pages say around a document
ENGLISH = {'page': 'Page {} of {}', 'bank': 'Bank details', 'copy': 'COPY'}
BLOCK = {'table', 'p', 'dl', 'ul', 'ol', 'div', 'section', 'h2', 'h3'}  # what stands on its own lines, also inside a cell
URL = re.compile(r'(https?://[^\s<>"]+[^\s<>".,;:!?)])')
FIELD = re.compile(r'\{(\w+)\}')
BOLD = re.compile(r'\*\*(.+?)\*\*', re.S)
LEADING = 1.25  # a text's lines, apart by its size times this, unless it sets its own; as the editor shows them (paper.js)

HOCHSCHULE, BANK, PERSON, DOKUMENT, SEITE = 'Hochschule', 'Bankverbindung der Hochschule', 'Person, um die es geht', 'Dieses Dokument', 'Seite'
FIELDS = {  # the placeholders a text on a Briefbogen may hold, by where their value comes from: (from, label, what it says).
    # The Hochschule's are its settings, the same on every document; a document's and a page's differ from one to the next.
    'hochschule': (HOCHSCHULE, 'Name', 'Der Name der Hochschule'),
    'hochschule_kurz': (HOCHSCHULE, 'Kurzname', 'Ihr Kurzname, sonst ihr Name'),
    'hochschule_anschrift': (HOCHSCHULE, 'Anschrift', 'Ihre Anschrift, Zeile für Zeile'),
    'hochschule_anschrift_zeile': (HOCHSCHULE, 'Anschrift in einer Zeile', 'Ihre Anschrift in einer Zeile, für die Rücksendeangabe'),
    'hochschule_ort': (HOCHSCHULE, 'Ort', 'Ihr Ort'),
    'hochschule_email': (HOCHSCHULE, 'E-Mail', 'Ihre E-Mail-Adresse'),
    'hochschule_website': (HOCHSCHULE, 'Website', 'Ihre Website, ohne https://'),
    'hochschule_bank': (BANK, 'Überschrift', 'Der Zahlungsempfänger, wo er anders heißt als die Hochschule, sonst „Bankverbindung“; leer ohne IBAN'),
    'hochschule_iban': (BANK, 'IBAN', 'Die IBAN, auf die Studierende den Semesterbeitrag überweisen'),
    'hochschule_bic': (BANK, 'BIC', 'Die BIC dazu'),
    'person_name': (PERSON, 'Name', 'Wem es gilt oder wen es bescheinigt: Studierende, Bewerbende, Lehrende; leer auf Listen wie der Notenliste'),
    'person_matrikelnummer': (PERSON, 'Matrikelnummer', 'Ihre Matrikelnummer; leer, wo sie keine hat, wie Lehrende und Bewerbende'),
    'dokument_titel': (DOKUMENT, 'Titel', 'Seine Überschrift, z. B. „Immatrikulationsbescheinigung“ oder der Betreff des Briefs'),
    'dokument_amt': (DOKUMENT, 'Amt', 'Das Amt, das es ausstellt, z. B. „Prüfungsamt“'),
    'dokument_aktenzeichen': (DOKUMENT, 'Aktenzeichen', 'Das Aktenzeichen eines Bescheids; leer bei anderen'),
    'dokument_datum': (DOKUMENT, 'Datum', 'Der Tag, an dem es ausgestellt ist'),
    'seitenzahl': (SEITE, 'Seite x von y', '„Seite 2 von 3“, englisch „Page 2 of 3“; leer, wo das Dokument eine Seite hat'),
    'seite': (SEITE, 'Seite', 'Die Nummer dieser Seite'),
    'seiten': (SEITE, 'Seiten', 'Wie viele Seiten das Dokument hat'),
}
PAGED = {'seitenzahl', 'seite', 'seiten'}  # known once the document is set: drawn last (Numbered)

DOCUMENTS = {  # key (data-paper on the template's .document): (what it is, its standard Briefbogen)
    'brief': ('Briefe und Bescheide', 'brief'),
    'immatrikulation': ('Immatrikulationsbescheinigung', 'bescheinigung'),
    'studienzeiten': ('Studienzeitbescheinigung', 'bescheinigung'),
    'exmatrikulation': ('Exmatrikulationsbescheinigung', 'bescheinigung'),
    'leistungen': ('Leistungsübersicht und Zeugnis', 'bescheinigung'),
    'urkunde': ('Urkunde', 'urkunde'),
    'supplement': ('Diploma Supplement', 'bescheinigung'),
    'notenliste': ('Notenliste', 'bescheinigung'),
    'leihvertrag': ('Leihvertrag', 'bescheinigung'),
    'vordruck': ('Vordruck der Kommission', 'bescheinigung'),
}
LETTERS = {'brief'}  # documents with an address: on a Briefbogen with an address window
OWN_LETTER = 'bescheid:'  # in Institution.papers, with a text's key: a Bescheid on a Briefbogen of its own, not that of all letters


def element(kind, key, x, y, w, h, pages='all', **fields):
    return {'id': key, 'type': kind, 'x': x, 'y': y, 'w': w, 'h': h, 'pages': pages, **fields}


def text(key, x, y, w, h, words, size, pages='all', **fields):
    return element('text', key, x, y, w, h, pages, text=words, size=size, **{'colour': 'muted'} | fields)


LATER = [  # every Briefbogen's following pages: what it is and whom it is about, over a hairline, so no page goes astray
    text('k1', 25, 11.4, 100, 4, '{dokument_titel}', 7.5, 'later'),
    text('k2', 125, 11.4, 65, 4, '{person_name}', 7.5, 'later', align='right'),
    element('box', 'k3', 25, 16, 165, 0.18, 'later', colour='line', fill=True, line=0),
]
FOOTER = [  # name and address, how to reach the Hochschule, where to pay: three columns under a hairline; the page
    element('box', 'f1', 25, 270, 165, 0.18, colour='line', fill=True, line=0),
    text('f2', 25, 271.6, 55, 16, '**{hochschule}**\n{hochschule_anschrift}', 7, leading=1.35),
    text('f3', 80, 271.6, 55, 16, '{hochschule_email}\n{hochschule_website}', 7, leading=1.35),
    text('f4', 135, 271.6, 55, 16, '{hochschule_bank}\nIBAN {hochschule_iban}\nBIC {hochschule_bic}', 7, leading=1.35),
    text('f5', 125, 263.5, 65, 4, '{seitenzahl}', 7.5, align='right'),
]
SANS = {'font': 'ptsans', 'heading': 'ptsans', 'size': 10, 'title': 19, 'bar': True, 'align': 'left', 'middle': False, 'marks': False}
STANDARD = {  # key: (name, design): ConCampus's own Briefbögen, until the Hochschule keeps its own under the key
    'brief': ('Brief', {'style': SANS | {'marks': True}, 'elements': [
        element('wordmark', 'm', 25, 14, 100, 17, 'first', size=15, align='left'),
        text('r', 25, 57.4, 85, 3, '{hochschule_kurz} · {hochschule_anschrift_zeile}', 6.5, 'first', underline=True),
        element('address', 'a', 25, 63.5, 80, 26.5, 'first', size=10),
        element('info', 'i', 125, 47, 65, 40, 'first', size=8.5),
        element('body', 'b1', 25, 98, 165, 164, 'first'),
        element('body', 'b2', 25, 26, 165, 236, 'later'),
        *LATER, *FOOTER,
    ]}),
    'bescheinigung': ('Bescheinigung', {'style': SANS, 'elements': [
        element('wordmark', 'm', 25, 14, 100, 17, 'first', size=15, align='left'),
        text('o', 105, 14, 85, 16, '**{dokument_amt}**\n{hochschule_anschrift}', 8.5, 'first', align='right', leading=1.35),
        element('body', 'b1', 25, 44, 165, 218, 'first'),
        element('body', 'b2', 25, 26, 165, 236, 'later'),
        *LATER, *FOOTER,
    ]}),
    'urkunde': ('Urkunde', {'style': SANS | {'font': 'ptserif', 'heading': 'ptserif', 'size': 12, 'title': 30, 'align': 'center', 'middle': True}, 'elements': [
        element('box', 'r1', 12, 12, 186, 273, colour='accent', fill=False, line=1.2),
        element('box', 'r2', 14.5, 14.5, 181, 268, colour='accent', fill=False, line=0.4),
        element('wordmark', 'm', 30, 32, 150, 17, 'first', size=17, align='center'),
        element('body', 'b1', 28, 50, 154, 217, 'first'),
        element('body', 'b2', 28, 30, 154, 237, 'later'),
    ]}),
}


# ---- fonts ---------------------------------------------------------------------------------------------------------

def own_fonts():
    """The Hochschule's own families: {key: (name, {(weight, italic): Artwork})}."""
    found = {}
    for art in Artwork.objects.filter(kind=Artwork.Kind.FONT):
        found.setdefault(OWN + slugify(art.name), (art.name, {}))[1][(art.weight or 400, art.italic)] = art
    return found


def families():
    """Every family a Briefbogen may set: {key: name}, ConCampus's first."""
    return {key: name for key, (name, _) in FAMILIES.items()} | {key: name for key, (name, _) in own_fonts().items()}


def faces(key, own=None):
    """The faces of family `key`: {(weight, italic): (ReportLab's name, file)}; of an unknown one, PT Sans's."""
    if key in FAMILIES:
        return {face: (f'{FAMILIES[key][1]}-{stem}', FONTS / f'{FAMILIES[key][1]}-{stem}.ttf') for face, stem in FACES.items()}
    own = own if own is not None else own_fonts()
    if key in own and own[key][1]:
        return {face: (f'Own{art.pk}', Path(art.file.path)) for face, art in own[key][1].items()}
    return faces('ptsans')


def nearest(found, weight, italic):
    """Of a family's faces, the one nearest in weight with this slant, else upright; the heavier where two are as near."""
    slant = [f for f in found if f[1] == italic] or [f for f in found if not f[1]] or list(found)
    return min(slant, key=lambda f: (abs(f[0] - weight), -f[0]))


def font(key, weight=400, italic=False, own=None):
    """ReportLab's name for family `key` in the face nearest to `weight` and `italic`, registered on first use, with its
    family: **bold** in it is the face of 700, or of 900 where it is heavy already, as near as the family has them."""
    found = faces(key, own)
    pick = lambda w, i: found[nearest(found, w, i)][0]  # noqa: E731
    heavy = 700 if weight < 600 else 900
    names = {'normal': pick(weight, False), 'bold': pick(heavy, False), 'italic': pick(weight, True), 'boldItalic': pick(heavy, True)}
    files = {name: path for name, path in found.values()}
    for name in names.values():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, files[name]))
    registerFontFamily(names['normal'], **names)
    addMapping(names['normal'], 0, 0, names['normal'])  # the face itself stays the family's upright one, whatever stood in for others
    return pick(weight, italic)


# ---- uploads -------------------------------------------------------------------------------------------------------

def opened(data):
    """A font file as fontTools reads it: TrueType, OpenType or WOFF; else ValidationError, saying what it is instead."""
    head = data[:4]
    if head == b'wOF2':
        raise ValidationError('WOFF2 lässt sich nicht lesen; bitte dieselbe Schrift als TTF oder OTF.')
    if head == b'ttcf':
        raise ValidationError('Eine Sammlung mehrerer Schriften (.ttc); bitte die einzelnen Dateien.')
    if head not in (b'\x00\x01\x00\x00', b'true', b'OTTO', b'wOFF'):
        raise ValidationError('Keine Schriftdatei: TTF, OTF oder WOFF.')
    if head == b'wOFF' and int.from_bytes(data[16:20], 'big') > 40 * 1024 * 1024:  # what it says it unpacks to
        raise ValidationError('Größer als 40 MB, entpackt.')
    try:
        font = Tools(BytesIO(data), lazy=False)
        font.flavor = None  # saved as plain TrueType, not WOFF
        return font
    except Exception:  # a malformed file raises struct, index, value and fontTools' own errors
        raise ValidationError('Die Datei lässt sich nicht als Schrift lesen.') from None


STYLE_WORDS = re.compile(r'\b(thin|hairline|extra ?light|ultra ?light|semi ?light|light|regular|normal|book|medium|semi ?bold|demi ?bold|extra ?bold|ultra ?bold|bold|black|heavy|italic|oblique)\b', re.I)


def statics(font):
    """A font's faces as static fonts: [(font, weight to the hundred, italic, what sets its family apart)]. A static font
    as it is, its weight and slant as it states them. A variable one by the styles it names (as many as 36): those
    of another width or the like, as Condensed, a family of their own; of two that round to the same weight, the one
    nearer to it, as Regular before SemiLight. At most 24 styles, as making each takes about a second."""
    def rounded(weight):
        return min(900, max(100, int(weight / 100 + 0.5) * 100))
    if 'fvar' not in font:
        upright = not (('OS/2' in font and font['OS/2'].fsSelection & 1) or font['post'].italicAngle)
        return [(font, rounded(font['OS/2'].usWeightClass if 'OS/2' in font else 400), not upright, '')]
    axes = {a.axisTag: a.defaultValue for a in font['fvar'].axes}
    styles = [(dict(i.coordinates), font['name'].getDebugName(i.subfamilyNameID) or '') for i in font['fvar'].instances[:24]] or [(axes, '')]  # each takes a second
    chosen = {}
    for where, name in styles:
        weight = where.get('wght', 400)
        italic = where.get('ital', 0) >= 0.5 or where.get('slnt', 0) != 0 or 'italic' in name.lower()
        other = tuple(sorted((tag, value) for tag, value in where.items() if tag not in ('wght', 'ital', 'slnt') and value != axes.get(tag)))
        apart = ' '.join(STYLE_WORDS.sub('', name).split()) if other else ''
        key = (other, rounded(weight), italic)
        if key not in chosen or abs(weight - rounded(weight)) < abs(chosen[key][0].get('wght', 400) - rounded(weight)):
            chosen[key] = (where, apart)
    groups = {}  # a family apart takes its name from its upright style nearest to Regular
    for (other, weight, italic), (where, apart) in sorted(chosen.items(), key=lambda kv: (abs(kv[0][1] - 400), kv[0][2])):
        groups.setdefault(other, apart)
    return [(instancer.instantiateVariableFont(font, where), weight, italic, groups[other]) for (other, weight, italic), (where, _) in chosen.items()]


def quadratic(font):
    """A font with PostScript (CFF) outlines redrawn as TrueType ones, the only ones ReportLab embeds: each curve as
    quadratic ones within a unit of the em, as fontTools' otf2ttf does it."""
    order, drawn = font.getGlyphOrder(), {}
    shapes = font.getGlyphSet()
    for name in order:
        pen = TTGlyphPen(shapes)
        shapes[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        drawn[name] = pen.glyph()
    font['loca'] = newTable('loca')
    font['glyf'] = glyf = newTable('glyf')
    glyf.glyphOrder, glyf.glyphs = order, drawn
    for tag in ('CFF ', 'CFF2', 'VORG'):
        if tag in font:
            del font[tag]
    glyf.compile(font)
    for name, glyph in glyf.glyphs.items():
        if hasattr(glyph, 'xMin'):
            font['hmtx'][name] = (font['hmtx'][name][0], glyph.xMin)
    font['maxp'] = maxp = newTable('maxp')
    maxp.tableVersion, maxp.maxZones = 0x00010000, 1
    for field in ('maxTwilightPoints', 'maxStorage', 'maxFunctionDefs', 'maxInstructionDefs', 'maxStackElements', 'maxSizeOfInstructions'):
        setattr(maxp, field, 0)
    maxp.maxComponentElements = max((len(getattr(g, 'components', [])) for g in drawn.values()), default=0)
    maxp.compile(font)
    post = font['post']
    post.formatType, post.extraNames, post.mapping, post.glyphOrder = 2.0, [], {}, order
    try:
        post.compile(font)
    except OverflowError:
        post.formatType = 3
    font.sfntVersion = '\x00\x01\x00\x00'


def accept_font(upload):
    """The faces in an uploaded font file, each checked and made TrueType that may be embedded:
    [(file to keep, family, weight, italic)]; else ValidationError. A variable font gives one face per named style.
    Family, weight and slant are read from the font."""
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError('Größer als 10 MB.')
    font, found = opened(upload.read()), []
    family = (font['name'].getDebugName(16) or font['name'].getDebugName(1) or Path(upload.name).stem).strip()[:80]
    if 'OS/2' in font and font['OS/2'].fsType & 0x0002 and not font['OS/2'].fsType & 0x000C:
        raise ValidationError('Ihre Lizenz verbietet, sie in Dokumente einzubetten (fsType „Restricted License“).')
    for static, weight, italic, apart in statics(font):
        if 'glyf' not in static:
            quadratic(static)
        # a PostScript name of its own: ReportLab takes a face for any other of the same name, as a variable font's
        # styles all have, or a face replaced has with the one before it (pdfmetrics.registerFont, _dynFaceNames)
        own = f'{re.sub(r"[^A-Za-z0-9]", "", family)[:40] or "Schrift"}-{weight}{"Italic" if italic else ""}-{secrets.token_hex(4)}'
        static['name'].removeNames(nameID=6)
        static['name'].setName(own, 6, 3, 1, 0x409)
        static['name'].setName(own, 6, 1, 0, 0)
        out = BytesIO()
        static.save(out)
        data = out.getvalue()
        try:
            probe = TTFont('probe', BytesIO(data))
            probe.face.makeSubset(list(range(32, 127)))  # as a PDF embeds it: refused where the font forbids it
        except Exception as error:  # ReportLab's TTFError, and struct and index errors of what it cannot read
            raise ValidationError(f'Diese Schrift lässt sich nicht in PDF-Dokumente einbetten: {error}') from None
        name = f'{family} {apart}'.strip()[:80]
        found.append((ContentFile(data, f'{slugify(name) or "schrift"}-{weight}{"i" if italic else ""}.ttf'), name, weight, italic))
    return found


def accept(upload):
    """An uploaded picture, checked and drawn anew from its pixels, PNG or JPEG, so nothing it carried besides them is
    kept: (file to keep, width, height); else ValidationError."""
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError('Größer als 5 MB.')
    data = upload.read()
    Image.MAX_IMAGE_PIXELS = 40_000_000  # a picture larger than any letterhead needs is refused, not decoded
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
        picture = Image.open(BytesIO(data))
        if picture.format not in ('PNG', 'JPEG'):
            raise ValidationError('Nur PNG oder JPEG.')
        picture.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise ValidationError('Kein Bild, das sich lesen lässt (PNG oder JPEG).') from None
    picture.thumbnail((4000, 4000))
    alpha = picture.mode in ('RGBA', 'LA', 'PA') or 'transparency' in picture.info
    fresh = Image.new('RGBA' if alpha else 'RGB', picture.size)
    fresh.paste(picture.convert('RGBA' if alpha else 'RGB'))
    out, png = BytesIO(), alpha or picture.format == 'PNG'
    fresh.save(out, 'PNG' if png else 'JPEG', **({'optimize': True} if png else {'quality': 92}))
    return ContentFile(out.getvalue(), f'{slugify(Path(upload.name).stem) or "bild"}.{"png" if png else "jpg"}'), *fresh.size


# ---- designs -------------------------------------------------------------------------------------------------------

def layouts():
    """Every Briefbogen: {key: (name, design, standard)}; the standard ones first, as the Hochschule keeps them where it does."""
    own = {layout.key: layout for layout in Layout.objects.all()}
    found = {key: (own[key].name if key in own else name, own[key].design if key in own else design, True) for key, (name, design) in STANDARD.items()}
    return found | {key: (layout.name, layout.design, False) for key, layout in own.items() if key not in STANDARD}


def chosen(institution=None, found=None):
    """The Briefbogen of each document: {document: key}; a Bescheid on one of its own also under OWN_LETTER and its text."""
    papers = (institution or Institution.get()).papers or {}
    found = found if found is not None else layouts()
    every = {doc: papers[doc] if papers.get(doc) in found else standard for doc, (_, standard) in DOCUMENTS.items()}
    return every | {slot: key for slot, key in papers.items() if slot.startswith(OWN_LETTER) and key in found}


def letter_paper(chosen, template):
    """The Briefbogen of a letter from the text `template`: its own, else that of all letters."""
    return chosen.get(OWN_LETTER + template, chosen['brief'])


def number(value, low, high, name):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f'{name}: keine Zahl.') from None
    if not low <= value <= high:
        raise ValidationError(f'{name}: zwischen {low:g} und {high:g}'.replace('.', ',') + '.')
    return round(value, 2)


def colour_of(value):
    if value in SWATCHES or isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return value
    raise ValidationError('Eine Farbe ist keine.')


def clean(design, letter=False):
    """A design as the editor sends it, checked field by field and reduced to what it may hold; ValidationError where it
    cannot be set. `letter`: it carries letters, so it needs an address window."""
    if not isinstance(design, dict) or not isinstance(design.get('style'), dict) or not isinstance(design.get('elements'), list):
        raise ValidationError('Kein Briefbogen.')
    fonts, style = families(), design['style']
    if style.get('font') not in fonts or style.get('heading') not in fonts:
        raise ValidationError('Eine Schrift gibt es nicht.')
    out = {'style': {
        'font': style['font'], 'heading': style['heading'], 'size': number(style.get('size'), 7, 16, 'Schriftgröße'),
        'title': number(style.get('title'), 10, 48, 'Titelgröße'), 'bar': bool(style.get('bar')),
        'align': 'center' if style.get('align') == 'center' else 'left', 'middle': bool(style.get('middle')), 'marks': bool(style.get('marks')),
    }, 'elements': []}
    if len(design['elements']) > 100:
        raise ValidationError('Mehr als 100 Elemente.')
    pictures = set(Artwork.objects.filter(kind=Artwork.Kind.PICTURE).values_list('pk', flat=True))
    for el in design['elements']:
        if not isinstance(el, dict) or el.get('type') not in ('body', 'text', 'wordmark', 'picture', 'box', 'address', 'info'):
            raise ValidationError('Ein Element gibt es nicht.')
        kind = el['type']
        x, y = number(el.get('x'), -5, PAGE[0], 'Links'), number(el.get('y'), -5, PAGE[1], 'Oben')
        w, h = number(el.get('w'), 0.1, PAGE[0] + 10, 'Breite'), number(el.get('h'), 0.1, PAGE[1] + 10, 'Höhe')
        clean_el = {
            'id': re.sub(r'[^\w-]', '', str(el.get('id', '')))[:16] or f'e{len(out["elements"])}', 'type': kind, 'x': x, 'y': y, 'w': w, 'h': h,
            'pages': el.get('pages') if el.get('pages') in ('first', 'later', 'all') else 'all',
            **({'locked': True} if el.get('locked') else {}),  # held fast in the editor; the PDF knows nothing of it
        }
        if kind == 'text':
            words = str(el.get('text', ''))[:1000]
            if unknown := set(FIELD.findall(words)) - set(FIELDS):
                raise ValidationError(f'Platzhalter gibt es nicht: {", ".join("{" + u + "}" for u in sorted(unknown))}.')
            clean_el |= {
                'text': words, 'size': number(el.get('size'), 4, 72, 'Schriftgröße'), 'leading': number(el.get('leading', LEADING), 0.8, 3, 'Zeilenabstand'),
                'font': el.get('font') if el.get('font') in fonts else '', 'colour': colour_of(el.get('colour', 'ink')),
                'align': el.get('align') if el.get('align') in ('left', 'center', 'right') else 'left',
                'weight': el['weight'] if el.get('weight') in WEIGHTS else 700 if el.get('bold') else 400,
                **{flag: bool(el.get(flag)) for flag in ('italic', 'underline', 'caps')},
            }
        elif kind == 'wordmark':
            clean_el |= {'size': number(el.get('size'), 6, 60, 'Schriftgröße'), 'align': el.get('align') if el.get('align') in ('left', 'center', 'right') else 'left'}
        elif kind == 'picture':
            if el.get('artwork') not in pictures:
                raise ValidationError('Ein Bild gibt es nicht mehr.')
            clean_el['artwork'] = el['artwork']
        elif kind == 'box':
            clean_el |= {'colour': colour_of(el.get('colour', 'accent')), 'fill': bool(el.get('fill')), 'line': number(el.get('line', 0.5), 0, 10, 'Linie')}
        elif kind in ('address', 'info'):
            clean_el['size'] = number(el.get('size'), 6, 14, 'Schriftgröße')
        out['elements'].append(clean_el)
    bodies = Counter(el['pages'] for el in out['elements'] if el['type'] == 'body')
    if bodies != Counter({'first': 1, 'later': 1}):
        raise ValidationError('Je ein Textbereich für die erste Seite und die Folgeseiten.')
    for el in out['elements']:
        if el['type'] == 'body' and (el['w'] < 40 or el['h'] < 40):
            raise ValidationError('Ein Textbereich ist kleiner als 4 × 4 cm.')
    if letter and not any(el['type'] == 'address' for el in out['elements']):
        raise ValidationError('Briefe brauchen ein Anschriftfeld.')
    return out


# ---- the pages around the text -------------------------------------------------------------------------------------

def named(url):
    """A web address as one reads it on paper: host and path where short enough to type, else the host."""
    parts = urlsplit(url)
    host = (parts.hostname or '').removeprefix('www.')
    return f'{host}{parts.path}'.rstrip('/') if len(host + parts.path) <= 48 and not parts.query else host


def values(institution, meta):
    """What each placeholder says on this document's pages, the page numbers aside (Sheet.paged)."""
    i, words = institution, meta['words']
    return {
        'hochschule': i.name, 'hochschule_kurz': i.short_name or i.name, 'hochschule_anschrift': i.address.strip(),
        'hochschule_anschrift_zeile': ' · '.join(line.strip() for line in i.address.splitlines() if line.strip()),
        'hochschule_ort': i.place, 'hochschule_email': i.email, 'hochschule_website': named(i.website) if i.website else '',
        'hochschule_bank': (i.payee if i.payee and i.payee != i.name else words['bank']) if i.iban else '', 'hochschule_iban': i.iban, 'hochschule_bic': i.bic,
        'person_name': meta.get('person', ''), 'person_matrikelnummer': meta.get('matrikel', ''),
        'dokument_titel': meta['title'], 'dokument_amt': '\n'.join(meta['office']), 'dokument_aktenzeichen': meta.get('reference', ''), 'dokument_datum': meta.get('date', ''),
    }


def filled(words, found):
    """A text's lines with its placeholders filled in; a line whose placeholders are all empty left out."""
    lines = []
    for line in words.splitlines():
        names = FIELD.findall(line)
        if names and not any(found.get(n) for n in names):
            continue
        lines += FIELD.sub(lambda m: str(found.get(m.group(1), m.group(0))), line).splitlines() or ['']
    return lines


def colour(value, accent):
    return HexColor(accent if value == 'accent' else SWATCHES.get(value) or value)


class Sheet:
    """The pages of one document around its text, as its Briefbogen places them."""

    def __init__(self, institution, design, meta, accent, own):
        self.i, self.design, self.meta, self.accent, self.own = institution, design, meta, accent, own
        self.style = design['style']
        self.values = values(institution, meta)
        logo = institution.logo
        self.logo = ImageReader(logo.path) if logo and logo.name.lower().endswith('.png') else None  # an SVG: the wordmark stands in
        arts = {el['artwork'] for el in design['elements'] if el['type'] == 'picture'}
        self.pictures = {a.pk: ImageReader(a.file.path) for a in Artwork.objects.filter(pk__in=arts)} if arts else {}

    def on(self, variant):
        return [el for el in self.design['elements'] if el['pages'] in ('all', variant)]

    def frame(self, variant, ident):
        body = next(el for el in self.on(variant) if el['type'] == 'body')
        return Frame(body['x'] * mm, HEIGHT - (body['y'] + body['h']) * mm, body['w'] * mm, body['h'] * mm, 0, 0, 0, 0, id=ident)

    def paged(self, el):
        return el['type'] == 'text' and bool(PAGED & set(FIELD.findall(el['text'])))

    def under(self, variant):
        """What a page shows under its text: drawn as it begins."""
        def draw(c, doc):
            c.meta, c.variant = self.meta, variant
            c.saveState()
            for el in self.on(variant):
                if not self.paged(el):
                    self.draw(c, el, self.values)
            if variant == 'first' and self.style['marks']:  # fold and hole marks at the left edge, DIN 5008
                c.setStrokeColor(MUTED)
                c.setLineWidth(0.4)
                for at, long in ((105, False), (148.5, True), (210, False)):
                    c.line(4 * mm, HEIGHT - at * mm, (10 if long else 7.5) * mm, HEIGHT - at * mm)
            c.restoreState()
        return draw

    def over(self, c, doc):
        """Across a copy, faint: a copy to read, not the signed and sealed original."""
        if self.meta.get('copy'):
            c.saveState()
            c.translate(WIDTH / 2, HEIGHT / 2)
            c.rotate(55)
            c.setFillColor(INK, alpha=0.05)
            c.setFont(font(self.style['heading'], 700, own=self.own), 96)
            c.drawCentredString(0, -32, self.meta['words']['copy'])
            c.restoreState()

    def numbered(self, c, variant, page, pages):
        """The texts that say which page this is, once the document's pages are counted."""
        found = self.values | {'seite': str(page), 'seiten': str(pages), 'seitenzahl': self.meta['words']['page'].format(page, pages) if pages > 1 else ''}
        c.saveState()
        for el in self.on(variant):
            if self.paged(el):
                self.draw(c, el, found)
        c.restoreState()

    def draw(self, c, el, found):
        x, top, w, h = el['x'] * mm, HEIGHT - el['y'] * mm, el['w'] * mm, el['h'] * mm
        kind = el['type']
        if kind == 'text':
            lines = filled(el['text'], found)
            if not any(line.strip() for line in lines):
                return
            words = '\n'.join(lines)  # bold over a placeholder of many lines holds for each of them
            markup = BOLD.sub(r'<b>\1</b>', escape(words.upper() if el.get('caps') else words)).replace('\n', '<br/>')
            if el.get('underline'):
                markup = f'<u>{markup}</u>'
            style = ParagraphStyle(
                'el', fontName=font(el.get('font') or self.style['font'], el.get('weight', 400), el.get('italic'), self.own), fontSize=el['size'],
                leading=el['size'] * el.get('leading', LEADING), textColor=colour(el['colour'], self.accent),
                alignment={'center': TA_CENTER, 'right': TA_RIGHT}.get(el.get('align'), 0),
            )
            p = Paragraph(markup, style)
            _, height = p.wrapOn(c, w, HEIGHT)
            p.drawOn(c, x, top - height)
        elif kind == 'box':
            ink = colour(el['colour'], self.accent)
            if el['fill']:
                c.setFillColor(ink)
                c.rect(x, top - h, w, h, stroke=0, fill=1)
            elif el['line']:
                c.setStrokeColor(ink)
                c.setLineWidth(el['line'])
                c.rect(x, top - h, w, h, stroke=1, fill=0)
        elif kind == 'picture' and el['artwork'] in self.pictures:
            self.image(c, self.pictures[el['artwork']], x, top, w, h, 'center')
        elif kind == 'wordmark':
            if self.logo:
                self.image(c, self.logo, x, top, w, h, el['align'])
            else:
                self.wordmark(c, el, x, top, w)
        elif kind == 'address':
            self.lines(c, [(line, el['size'], INK, False) for line in self.meta.get('address', [])[:6]], x, top, el['size'] * 1.3)
        elif kind == 'info':
            size, y = el['size'], top
            for n, line in enumerate(self.meta.get('office', [])):
                c.setFont(font(self.style['font'], 700 if n == 0 else 400, own=self.own), size)
                c.setFillColor(INK if n == 0 else MUTED)
                c.drawString(x, y - size, line)
                y -= size * 1.33
            y -= size * 0.9
            for label, value in self.meta.get('info', []):
                if value:
                    c.setFont(font(self.style['font'], own=self.own), size * 0.82)
                    c.setFillColor(MUTED)
                    c.drawString(x, y - size, label)
                    c.setFont(font(self.style['font'], own=self.own), size * 1.06)
                    c.setFillColor(INK)
                    c.drawString(x + min(24 * mm, w / 2.6), y - size, value)
                    y -= size * 1.53

    def lines(self, c, rows, x, top, leading):
        y = top
        for words, size, ink, bold in rows:
            c.setFont(font(self.style['font'], 700 if bold else 400, own=self.own), size)
            c.setFillColor(ink)
            c.drawString(x, y - size, words)
            y -= leading

    @staticmethod
    def image(c, picture, x, top, w, h, align):
        """A picture as large as its box holds it, its proportions kept."""
        pw, ph = picture.getSize()
        k = min(w / pw, h / ph)
        dw, dh = pw * k, ph * k
        left = x + {'center': (w - dw) / 2, 'right': w - dw}.get(align, 0)
        c.drawImage(picture, left, top - dh, dw, dh, mask='auto')

    def wordmark(self, c, el, x, top, w):
        """The Schriftzug, its later lines in small capitals and the accent colour, as on screen."""
        size, face = el['size'], font(self.style['font'], own=self.own)
        y, ink = top, colour('accent', self.accent)
        for n, line in enumerate(self.i.lines):
            if n == 0:
                runs = [(line, size)]
                y -= size
            else:
                runs = [(ch.upper(), size * (0.78 if ch.islower() else 1)) for ch in line]
                y -= size * 1.15
            width = sum(stringWidth(ch, face, s) + (size * 0.03 if n else 0) for ch, s in runs)
            cx = x + {'center': (w - width) / 2, 'right': w - width}.get(el['align'], 0)
            c.setFillColor(INK if n == 0 else ink)
            for ch, s in runs:
                c.setFont(face, s)
                c.drawString(cx, y, ch)
                cx += stringWidth(ch, face, s) + (size * 0.03 if n else 0)


class Numbered(Canvas):
    """A canvas that holds its pages back to number them, each document on its own: „Seite 2 von 3“."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        totals, seen = Counter(id(page.get('meta')) for page in self.pages), Counter()
        for page in self.pages:
            self.__dict__.update(page)
            meta = page.get('meta')
            seen[id(meta)] += 1
            if meta:
                meta['sheet'].numbered(self, page.get('variant', 'first'), seen[id(meta)], totals[id(meta)])
            super().showPage()
        super().save()


# ---- the text ------------------------------------------------------------------------------------------------------

def classes(el):
    return set((el.get('class') or '').split())


def esc(words, link='#000000'):
    """Text as ReportLab's paragraph markup takes it: escaped, without soft hyphens, which it would print; a web address
    a link, named so that it reads (named())."""
    parts = URL.split((words or '').replace('\xad', ''))
    return ''.join(
        f'<link href="{escape(part, {chr(34): "&quot;"})}" color="{link}">{escape(named(part))}</link>'
        if n % 2 and urlsplit(part).hostname else escape(part) for n, part in enumerate(parts)
    )


def qr(url, size=21 * mm):
    """A QR code to `url`, for a phone held to the paper."""
    code = QrCodeWidget(url, barLevel='M')
    x1, y1, x2, y2 = code.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(code)
    return drawing


def lines(el):
    """An element's text, line by line at its <br>s."""
    found, line = [], [el.text or '']
    for child in el:
        if child.tag == 'br':
            found.append(''.join(line))
            line = []
        else:
            line.append(child.text_content())
        line.append(child.tail or '')
    return [s for s in (' '.join(''.join(part).split()) for part in found + [line]) if s]


def px(el, name):
    """A length from the element's inline style in pixels (margin-top: 24px), as points; 0 if none."""
    for rule in (el.get('style') or '').split(';'):
        key, _, value = rule.partition(':')
        if key.strip() == name and value.strip().endswith('px'):
            return float(value.strip()[:-2]) * 0.75
    return 0


class Look:
    """Type and colour of a document's text, as its Briefbogen sets them (its `style`)."""

    def __init__(self, style, accent, own=None):
        self.accent, self.link, self.centred = HexColor(accent), accent, style['align'] == 'center'
        size, align = style['size'], TA_CENTER if style['align'] == 'center' else 0
        self.size, self.bar = size, style['bar']
        regular, bold = font(style['font'], own=own), font(style['font'], 700, own=own)
        heading = font(style['heading'], 700, own=own)
        self.regular, self.bold = regular, bold
        base = ParagraphStyle('body', fontName=regular, fontSize=size, leading=size * 1.45, spaceAfter=size * 0.7, alignment=align, textColor=INK)
        self.body = base
        self.muted = ParagraphStyle('muted', base, textColor=MUTED)
        self.small = ParagraphStyle('small', base, fontSize=size * 0.8, leading=size * 1.1, textColor=MUTED)
        self.large = ParagraphStyle('large', base, fontName=heading, fontSize=size * 1.6, leading=size * 2.1, spaceBefore=size * 0.4)
        title = style['title']
        self.title = ParagraphStyle('title', base, fontName=heading, fontSize=title, leading=title * 1.2, spaceAfter=title * 0.5)
        self.heading = ParagraphStyle('heading', base, fontName=heading, fontSize=size * 1.15, leading=size * 1.5, spaceBefore=size * 1.4, spaceAfter=size * 0.6, keepWithNext=True)
        self.term = ParagraphStyle('term', base, fontSize=size * 0.9, leading=size * 1.25, textColor=MUTED, spaceAfter=0)
        self.fact = ParagraphStyle('fact', base, fontSize=size, leading=size * 1.35, spaceAfter=0)
        self.cell = ParagraphStyle('cell', base, fontSize=size * 0.88, leading=size * 1.15, spaceAfter=0, alignment=0)
        self.number = ParagraphStyle('number', self.cell, alignment=TA_RIGHT)
        self.head = ParagraphStyle('head', self.cell, fontName=bold, fontSize=size * 0.72, leading=size * 0.95, textColor=MUTED)
        self.head_number = ParagraphStyle('head_number', self.head, alignment=TA_RIGHT)
        self.sign = ParagraphStyle('sign', base, fontSize=size if self.centred else size * 0.9, leading=size * 1.4 if self.centred else size * 1.25, textColor=INK if self.centred else MUTED, spaceAfter=0)


class Bar(Flowable):
    """The accent bar over a title, as the screen has it."""

    def __init__(self, width, colour, centred=False):
        super().__init__()
        self.bar, self.colour, self.centred = width, colour, centred

    def wrap(self, available, _):
        self.room = available
        return available, 9

    def draw(self):
        self.canv.setFillColor(self.colour)
        self.canv.rect((self.room - self.bar) / 2 if self.centred else 0, 6, self.bar, 2.6, stroke=0, fill=1)


class Centred(Flowable):
    """A document's text, upright in the middle of its text area, as an Urkunde stands; on one page."""

    def __init__(self, flowables):
        super().__init__()
        self.items = [f for item in flowables for f in (item._content if isinstance(item, KeepTogether) else [item])]

    def wrap(self, available, height):
        self.room, self.height = available, height
        self.sizes = [f.wrap(available, height) for f in self.items]
        self.inner = sum(h + f.getSpaceBefore() + f.getSpaceAfter() for f, (_, h) in zip(self.items, self.sizes))
        return available, max(self.inner, height - 1)

    def draw(self):
        y = self.height - max(0, (self.height - self.inner) / 2.4)  # a little above the middle, where the eye rests
        for f, (w, h) in zip(self.items, self.sizes):
            y -= f.getSpaceBefore() + h
            f.drawOn(self.canv, (self.room - w) / 2 if getattr(f, 'hAlign', 'LEFT') == 'CENTER' else 0, y)
            y -= f.getSpaceAfter()


def inline(el, look, size):
    """An element's content as paragraph markup: bold, italic, small and muted runs, line breaks; the rest as text."""
    parts = [esc(el.text, look.link)]
    for child in el:
        if child.tag in SKIP or 'no-print' in classes(child) or not isinstance(child.tag, str):
            parts.append(esc(child.tail, look.link))
            continue
        inner = '<br/>' if child.tag == 'br' else inline(child, look, size * 0.85 if child.tag == 'small' else size)
        if child.tag in ('b', 'strong'):
            inner = f'<b>{inner}</b>'
        elif child.tag in ('i', 'em'):
            inner = f'<i>{inner}</i>'
        elif child.tag == 'sup':
            inner = f'<super>{inner}</super>'
        elif child.tag == 'small':
            inner = f'<font size="{size * 0.85:.1f}">{inner}</font>'
        if {'muted', 'fail'} & classes(child):
            inner = f'<font color="{"#c4262e" if "fail" in classes(child) else "#5f5f5f"}">{inner}</font>'
        parts.append(inner + esc(child.tail, look.link))
    return ''.join(parts).strip()


def plain(el):
    return ' '.join(el.text_content().replace('\xad', '').split())


def widths(columns, total, look):
    """Column widths for a table: numbers and dates as wide as their widest cell or heading, the rest sharing what is
    left by their length, none narrower than its longest word."""
    size = look.cell.fontSize
    natural = [max([stringWidth(t, look.regular, size) + 10 for t in texts] + [head], default=20) for texts, _, head in columns]
    least = [max([stringWidth(word, look.regular, size) + 10 for t in texts for word in t.split()] + [head], default=20) for texts, _, head in columns]
    fixed = [tight for _, tight, _ in columns]
    given = [n if tight else 0 for n, tight in zip(natural, fixed)]
    left = total - sum(given)
    loose = [i for i, tight in enumerate(fixed) if not tight]
    want = sum(natural[i] for i in loose) or 1
    for i in loose:
        given[i] = max(least[i], left * natural[i] / want)
    scale = total / sum(given)
    return [w * scale for w in given]


def table(el, look, width):
    rows, spans, styles, header = [], [], [], 0
    for section in el:
        for tr in section if section.tag in ('thead', 'tbody', 'tfoot') else [section]:
            if tr.tag != 'tr':
                continue
            cells, col = [], 0
            for td in tr:
                if td.tag not in ('td', 'th'):
                    continue
                span = int(td.get('colspan') or 1)
                cells.append((td, span))
                if span > 1:
                    spans.append(('SPAN', (col, len(rows)), (col + span - 1, len(rows))))
                col += span
            if section.tag == 'thead':
                header += 1
            rows.append((tr, cells))
    if not rows:
        return []
    count = max(sum(span for _, span in cells) for _, cells in rows)
    columns = [([], True, 0) for _ in range(count)]
    for tr, cells in rows:
        col = 0
        for td, span in cells:
            if span == 1:
                texts, tight, head = columns[col]
                tight = tight and bool({'num', 'nowrap'} & classes(td) or td.tag == 'th' or not plain(td))
                if td.tag == 'th':
                    head = max(head, stringWidth(plain(td).upper(), look.bold, look.head.fontSize) + 8)
                columns[col] = (texts + ([plain(td)] if td.tag == 'td' else []), tight, head)
            col += span
    sizes = widths(columns, width, look)
    data = []
    for r, (tr, cells) in enumerate(rows):
        row, total = [], 'total' in classes(tr)
        for td, span in cells:
            number = 'num' in classes(td)
            style = (look.head_number if number else look.head) if td.tag == 'th' else look.number if number else look.cell
            markup = inline(td, look, style.fontSize)
            markup = markup.upper() if td.tag == 'th' else markup
            if 'muted' in classes(td) or 'empty' in classes(td):
                markup = f'<font color="#5f5f5f">{markup}</font>'
            if 'fail' in classes(td):
                markup = f'<font color="#c4262e">{markup}</font>'
            row.append(Paragraph(f'<b>{markup}</b>' if total else markup, style))
            row.extend([''] * (span - 1))
        row.extend([''] * (count - len(row)))
        data.append(row)
        if total:
            styles.append(('LINEABOVE', (0, r), (-1, r), 0.9, INK))
    t = Table(data, colWidths=sizes, repeatRows=header, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
        *([('LINEBELOW', (0, header - 1), (-1, header - 1), 1.2, INK)] if header else []),
        *spans, *styles,
    ]))
    return [Spacer(1, 6), t, Spacer(1, 10)]


def content(el, look, style, width):
    """What an element holds, where a table or list may stand among its words: the words as paragraphs, the rest as
    blocks() sets them, `width` wide."""
    if not any(isinstance(c.tag, str) and c.tag in BLOCK for c in el):
        return Paragraph(inline(el, look, style.fontSize), style)
    found, run = [], lxml_html.Element('span')
    run.text = el.text
    for child in el:
        if isinstance(child.tag, str) and child.tag in BLOCK:
            if markup := inline(run, look, style.fontSize):
                found.append(Paragraph(markup, style))
            holder, run = lxml_html.Element('div'), lxml_html.Element('span')
            holder.append(deepcopy(child))
            run.text = child.tail
            found += blocks(holder, look, width)
        else:
            run.append(deepcopy(child))
    if markup := inline(run, look, style.fontSize):
        found.append(Paragraph(markup, style))
    return found


def facts(el, look, width):
    """A <dl class="facts">: a term to the left, muted, what it says to the right."""
    pairs, term, left = [], None, min(48 * mm, width * 0.32)
    for child in el:
        if child.tag == 'dt':
            term = Paragraph(inline(child, look, look.term.fontSize), look.term)
        elif child.tag == 'dd':
            pairs.append([term or '', content(child, look, look.fact, width - left - 8)])
            term = None
    if not pairs:
        return []
    t = Table(pairs, colWidths=[left, width - left], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
    ]))
    return [Spacer(1, 4), t, Spacer(1, 8)]


def sign(el, look, width):
    """A <div class="sign">: place and date to the left, who signs to the right."""
    parts = [s for s in el if isinstance(s.tag, str)]
    spans = [Paragraph(inline(s, look, look.sign.fontSize), ParagraphStyle('s', look.sign, alignment=TA_CENTER if look.centred else TA_RIGHT if i else 0)) for i, s in enumerate(parts)]
    if not spans:
        return []
    t = Table([spans], colWidths=[width / len(spans)] * len(spans), hAlign='LEFT')
    t.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    return [Spacer(1, 26), KeepTogether([t])]


def signatures(el, look, width):
    """A <div class="signatures">: a line to sign above each name, three to a row; centred, no wider than a hand signs."""
    names = [Paragraph(inline(s, look, look.sign.fontSize), ParagraphStyle('n', look.sign, alignment=TA_CENTER)) for s in el if isinstance(s.tag, str)]
    if not names:
        return []
    per = min(3, len(names))
    rows = [names[i:i + per] + [''] * (per - len(names[i:i + per])) for i in range(0, len(names), per)]
    gap = 24 * mm if look.centred else 10 * mm
    width = min((width - gap * (per - 1)) / per, 66 * mm if look.centred else width)
    data, style = [], [('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 5)]
    for row in rows:
        r = len(data)
        data.append([x for name in row for x in (name, '')][:-1])
        style += [('LINEABOVE', (2 * i, r), (2 * i, r), 0.7, INK) for i, name in enumerate(row) if name]
    t = Table(data, colWidths=[w for _ in range(per) for w in (width, gap)][:-1], hAlign='CENTER')
    t.setStyle(TableStyle(style + [('BOTTOMPADDING', (0, 0), (-1, -2), 18 * mm)]))  # room to sign above the next row
    return [Spacer(1, 20 * mm), KeepTogether([t])]


def blocks(el, look, width):
    """The flowables of an element's children: headings, paragraphs, facts, tables, lists, the lines to sign."""
    found = []
    for child in el:
        if not isinstance(child.tag, str) or child.tag in SKIP:
            continue
        kind = classes(child)
        if kind & {'no-print', 'letterhead', 'address', 'dated', 'actions'}:
            continue
        if before := px(child, 'margin-top'):
            found.append(Spacer(1, before * 0.8))
        if child.tag == 'h1':
            markup = inline(child, look, look.title.fontSize)
            bar = min(width, max(30 * mm, stringWidth(plain(child), look.title.fontName, look.title.fontSize)))
            found.append(KeepTogether(([Bar(bar, look.accent, look.centred)] if look.bar else []) + [Paragraph(markup, look.title)]))
        elif child.tag in ('h2', 'h3'):
            found.append(Paragraph(inline(child, look, look.heading.fontSize), look.heading))
        elif child.tag == 'p':
            markup = inline(child, look, look.size)
            if not markup:
                continue
            only_small = len(child) == 1 and child[0].tag == 'small' and not (child.text or '').strip()
            style = look.small if only_small else look.large if px(child, 'font-size') >= 15 else look.muted if 'muted' in kind else look.body
            paragraph = Paragraph(f'<font color="#c4262e">{markup}</font>' if 'fail' in kind else markup, style)
            if link := child.get('data-qr'):  # a code to check the document by, beside the words that say so
                t = Table([[paragraph, qr(link)]], colWidths=[width - 26 * mm, 26 * mm], hAlign='LEFT')
                t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'BOTTOM'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('ALIGN', (1, 0), (1, 0), 'RIGHT')]))
                paragraph = t
            found.append(paragraph)
        elif child.tag == 'dl':
            found += facts(child, look, width)
        elif child.tag == 'table':
            found += table(child, look, width)
        elif child.tag in ('ul', 'ol'):
            items = [ListItem(Paragraph(inline(li, look, look.size), look.body)) for li in child if li.tag == 'li']
            found.append(ListFlowable(items, bulletType='bullet' if child.tag == 'ul' else '1', bulletFontSize=look.size * 0.8, leftIndent=12))
        elif 'sign' in kind:
            found += sign(child, look, width)
        elif 'signatures' in kind:
            found += signatures(child, look, width)
        elif child.tag in ('div', 'section'):
            if child.tag == 'section':
                found.append(CondPageBreak(30 * mm))
            found += blocks(child, look, width)
        elif markup := inline(child, look, look.size):
            found.append(Paragraph(markup, look.body))
    return found


# ---- the PDF -------------------------------------------------------------------------------------------------------

def document_of(el):
    """Which of DOCUMENTS a `.document` is: its data-paper, else by its kind."""
    if (key := el.get('data-paper')) in DOCUMENTS:
        return key
    kind = classes(el)
    return 'brief' if 'letter' in kind else 'urkunde' if 'ceremony' in kind else 'immatrikulation'


def pdf(page, title='', design=None):
    """The `.document`s of a rendered page as one PDF, each from a new page on its Briefbogen, with its own page
    numbers; `design`: every one on this one instead, to try it (documents.views.preview)."""
    institution = Institution.get()
    accent, own = institution.accent or '#008a00', own_fonts()
    found = layouts() if design is None else {}
    papers = chosen(institution, found) if design is None else {}
    root = lxml_html.document_fromstring(page)
    story, templates = [], []
    for index, el in enumerate(root.find_class('document')):
        kind = classes(el)
        doc = document_of(el)
        layout = design or found[letter_paper(papers, el.get('data-template', '')) if doc in LETTERS else papers[doc]][1]
        look = Look(layout['style'], accent, own)
        head = next(iter(el.find_class('letterhead')), None)
        office = [line for line in lines(next(iter(head.find_class('muted')), head)) if line not in institution.address.splitlines()] if head is not None and head.find_class('muted') else []
        meta = {
            'office': office, 'title': next((plain(h) for h in el.iter('h1', 'h2')), title), 'copy': 'copy' in kind,
            'words': ENGLISH if el.get('lang') == 'en' else GERMAN, 'date': date_format(timezone.localdate(), 'j. F Y'),
            'person': el.get('data-person', ''), 'matrikel': el.get('data-matrikel', ''),
        }
        address, dated = next(iter(el.find_class('address')), None), next(iter(el.find_class('dated')), None)
        if address is not None:
            meta['address'] = lines(address)
        if dated is not None:
            meta['office'] = [dated.get('data-office')] if dated.get('data-office') else office
            meta['reference'], meta['date'] = dated.get('data-reference', ''), dated.get('data-date', '') or meta['date']
            meta['info'] = [('Aktenzeichen', meta['reference']), ('Datum', meta['date'])]
        sheet = Sheet(institution, layout, meta, accent, own)
        meta['sheet'] = sheet
        templates += [
            PageTemplate(id=f'first{index}', frames=[sheet.frame('first', f'first{index}')], onPage=sheet.under('first'), onPageEnd=sheet.over, autoNextPageTemplate=f'later{index}'),
            PageTemplate(id=f'later{index}', frames=[sheet.frame('later', f'later{index}')], onPage=sheet.under('later'), onPageEnd=sheet.over),
        ]
        if story:
            story += [NextPageTemplate(f'first{index}'), PageBreak()]
        flowables = blocks(el, look, min(sheet.frame(variant, 'w').width for variant in ('first', 'later')))
        story += [Centred(flowables)] if layout['style']['middle'] else flowables
    if not story:
        return pdf('<div class="document"><p>Kein Dokument.</p></div>', title, design)
    buffer = BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4, title=title, author=institution.name, creator='ConCampus', subject=title, lang='de-DE')
    doc.addPageTemplates(templates)
    doc.build(story, canvasmaker=Numbered)
    return buffer.getvalue()


def letter(message):
    """A message from the Postfach as a letter, its PDF: a Bescheid as it is issued (students.Study.notify)."""
    return pdf(render_to_string('core/_letter.html', {'letter': message, 'institution': Institution.get()}), message.subject)


def respond(data, name):
    """A PDF to open in the browser, saved under `name`."""
    response = HttpResponse(data, content_type='application/pdf')
    response['Content-Disposition'] = content_disposition_header(False, f'{name}.pdf'.replace('/', '-'))
    return response


def answer(request, template, context, name):
    """The page; with ?pdf the same, as a PDF named `name`."""
    if 'pdf' not in request.GET:
        return render(request, template, context)
    return respond(pdf(render_to_string(template, context, request), name), name)
