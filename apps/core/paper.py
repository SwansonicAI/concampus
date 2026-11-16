"""Documents as PDF: letters after DIN 5008 (Form B), Bescheinigungen, Zeugnisse and Urkunden, set from the templates
that show them on screen. Each `.document` of a rendered page becomes one; only the markup those templates use is read
(blocks(), inline()), every text is escaped, and nothing is fetched. ReportLab sets the pages, in PT Sans and PT Serif
(OFL, core/fonts)."""
import re
from collections import Counter
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.http import content_disposition_header
from lxml import html as lxml_html
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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

from .models import Institution

FONTS = Path(__file__).parent / 'fonts'
for family in ('PTSans', 'PTSerif'):
    for face in ('Regular', 'Bold', 'Italic', 'BoldItalic'):
        pdfmetrics.registerFont(TTFont(f'{family}-{face}', FONTS / f'{family}-{face}.ttf'))
    registerFontFamily(family, normal=f'{family}-Regular', bold=f'{family}-Bold', italic=f'{family}-Italic', boldItalic=f'{family}-BoldItalic')

INK, MUTED, LINE, RED = HexColor('#000000'), HexColor('#5f5f5f'), HexColor('#d6d6d6'), HexColor('#c4262e')
WIDTH, HEIGHT = A4
LEFT, RIGHT = 25 * mm, 20 * mm  # DIN 5008: the text from 25 mm, to 20 mm before the edge
TEXT = WIDTH - LEFT - RIGHT
FOOT = 27 * mm  # the footer's height from the bottom edge
SKIP = {'form', 'button', 'script', 'style', 'input', 'select', 'textarea'}
GERMAN = {'page': 'Seite {} von {}', 'bank': 'Bankverbindung', 'copy': 'ABSCHRIFT'}  # what the pages say around a document
ENGLISH = {'page': 'Page {} of {}', 'bank': 'Bank details', 'copy': 'COPY'}
BLOCK = {'table', 'p', 'dl', 'ul', 'ol', 'div', 'section', 'h2', 'h3'}  # what stands on its own lines, also inside a cell
URL = re.compile(r'(https?://[^\s<>"]+[^\s<>".,;:!?)])')


def classes(el):
    return set((el.get('class') or '').split())


def named(url):
    """A web address as one reads it on paper: host and path where short enough to type, else the host."""
    parts = urlsplit(url)
    host = parts.hostname.removeprefix('www.')
    return f'{host}{parts.path}'.rstrip('/') if len(host + parts.path) <= 48 and not parts.query else host


def esc(text, colour='#000000'):
    """Text as ReportLab's paragraph markup takes it: escaped, without soft hyphens, which it would print; a web address
    a link, named so that it reads (named())."""
    parts = URL.split((text or '').replace('\xad', ''))
    return ''.join(
        f'<link href="{escape(part, {chr(34): "&quot;"})}" color="{colour}">{escape(named(part))}</link>'
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
    """Type and colour of one document: sans for letters and Bescheinigungen, serif and centred for an Urkunde."""

    def __init__(self, accent, ceremony=False):
        self.accent, self.link, self.ceremony = HexColor(accent), accent, ceremony
        face, size = ('PTSerif', 12) if ceremony else ('PTSans', 10)
        align = TA_CENTER if ceremony else 0
        self.face, self.size = face, size
        base = ParagraphStyle('body', fontName=f'{face}-Regular', fontSize=size, leading=size * 1.45, spaceAfter=size * 0.7, alignment=align, textColor=INK)
        self.body = base
        self.muted = ParagraphStyle('muted', base, textColor=MUTED)
        self.small = ParagraphStyle('small', base, fontSize=8, leading=11, textColor=MUTED)
        self.large = ParagraphStyle('large', base, fontName=f'{face}-Bold', fontSize=size * (2 if ceremony else 1.6), leading=size * (2.5 if ceremony else 2.1), spaceBefore=size * 0.4)
        self.title = ParagraphStyle('title', base, fontName=f'{face}-Bold', fontSize=30 if ceremony else 19, leading=36 if ceremony else 23, spaceAfter=14 if ceremony else 10)
        self.heading = ParagraphStyle('heading', base, fontName=f'{face}-Bold', fontSize=11.5, leading=15, spaceBefore=14, spaceAfter=6, keepWithNext=True)
        self.term = ParagraphStyle('term', base, fontSize=9, leading=12.5, textColor=MUTED, spaceAfter=0)
        self.fact = ParagraphStyle('fact', base, fontSize=size, leading=size * 1.35, spaceAfter=0)
        self.cell = ParagraphStyle('cell', base, fontSize=8.8, leading=11.5, spaceAfter=0, alignment=0)
        self.number = ParagraphStyle('number', self.cell, alignment=TA_RIGHT)
        self.head = ParagraphStyle('head', self.cell, fontName=f'{face}-Bold', fontSize=7.2, leading=9.5, textColor=MUTED)
        self.head_number = ParagraphStyle('head_number', self.head, alignment=TA_RIGHT)
        self.sign = ParagraphStyle('sign', base, fontSize=size if ceremony else 9, leading=size * 1.4 if ceremony else 12.5, textColor=INK if ceremony else MUTED, spaceAfter=0)


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
    """An Urkunde's text, upright in the middle of the page under its head; on one page, as an Urkunde is."""

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


class Start(Flowable):
    """Where a document begins: what its pages show around it (Paper.chrome) from here on."""

    def __init__(self, meta):
        super().__init__()
        self.meta = meta

    def wrap(self, *_):
        return 0, 0

    def draw(self):
        self.canv.meta = self.meta


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


def widths(columns, total):
    """Column widths for a table: numbers and dates as wide as their widest cell or heading, the rest sharing what is
    left by their length, none narrower than its longest word."""
    natural = [max([stringWidth(text, 'PTSans-Regular', 8.8) + 10 for text in texts] + [head], default=20) for texts, _, head in columns]
    least = [max([stringWidth(word, 'PTSans-Regular', 8.8) + 10 for text in texts for word in text.split()] + [head], default=20) for texts, _, head in columns]
    fixed = [tight for _, tight, _ in columns]
    given = [n if tight else 0 for n, tight in zip(natural, fixed)]
    left = total - sum(given)
    loose = [i for i, tight in enumerate(fixed) if not tight]
    want = sum(natural[i] for i in loose) or 1
    for i in loose:
        given[i] = max(least[i], left * natural[i] / want)
    scale = total / sum(given)
    return [w * scale for w in given]


def table(el, look, width=TEXT):
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
                    head = max(head, stringWidth(plain(td).upper(), 'PTSans-Bold', 7.2) + 8)
                columns[col] = (texts + ([plain(td)] if td.tag == 'td' else []), tight, head)
            col += span
    sizes = widths(columns, width)
    data = []
    for r, (tr, cells) in enumerate(rows):
        row, col, total = [], 0, 'total' in classes(tr)
        for td, span in cells:
            number = 'num' in classes(td)
            style = (look.head_number if number else look.head) if td.tag == 'th' else look.number if number else look.cell
            text = inline(td, look, style.fontSize)
            text = text.upper() if td.tag == 'th' else text
            if 'muted' in classes(td) or 'empty' in classes(td):
                text = f'<font color="#5f5f5f">{text}</font>'
            if 'fail' in classes(td):
                text = f'<font color="#c4262e">{text}</font>'
            row.append(Paragraph(f'<b>{text}</b>' if total else text, style))
            row.extend([''] * (span - 1))
            col += span
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
            if text := inline(run, look, style.fontSize):
                found.append(Paragraph(text, style))
            holder, run = lxml_html.Element('div'), lxml_html.Element('span')
            holder.append(deepcopy(child))
            run.text = child.tail
            found += blocks(holder, look, width)
        else:
            run.append(deepcopy(child))
    if text := inline(run, look, style.fontSize):
        found.append(Paragraph(text, style))
    return found


def facts(el, look, width=TEXT):
    """A <dl class="facts">: a term to the left, muted, what it says to the right."""
    pairs, term = [], None
    for child in el:
        if child.tag == 'dt':
            term = Paragraph(inline(child, look, 9), look.term)
        elif child.tag == 'dd':
            pairs.append([term or '', content(child, look, look.fact, width - 48 * mm - 8)])
            term = None
    if not pairs:
        return []
    t = Table(pairs, colWidths=[48 * mm, width - 48 * mm], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
    ]))
    return [Spacer(1, 4), t, Spacer(1, 8)]


def sign(el, look, width=TEXT):
    """A <div class="sign">: place and date to the left, who signs to the right."""
    parts = [s for s in el if isinstance(s.tag, str)]
    spans = [Paragraph(inline(s, look, 9), ParagraphStyle('s', look.sign, alignment=TA_CENTER if look.ceremony else TA_RIGHT if i else 0)) for i, s in enumerate(parts)]
    if not spans:
        return []
    t = Table([spans], colWidths=[width / len(spans)] * len(spans), hAlign='LEFT')
    t.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    return [Spacer(1, 26), KeepTogether([t])]


def signatures(el, look, width=TEXT):
    """A <div class="signatures">: a line to sign above each name, three to a row; on an Urkunde no wider than a hand
    signs."""
    names = [Paragraph(inline(s, look, 9), ParagraphStyle('n', look.sign, alignment=TA_CENTER)) for s in el if isinstance(s.tag, str)]
    if not names:
        return []
    per = min(3, len(names))
    rows = [names[i:i + per] + [''] * (per - len(names[i:i + per])) for i in range(0, len(names), per)]
    gap = 10 * mm if not look.ceremony else 24 * mm
    width = min((width - gap * (per - 1)) / per, 66 * mm if look.ceremony else width)
    data, style = [], [('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 5)]
    for row in rows:
        r = len(data)
        data.append([x for name in row for x in (name, '')][:-1])
        style += [('LINEABOVE', (2 * i, r), (2 * i, r), 0.7, INK) for i, name in enumerate(row) if name]
    t = Table(data, colWidths=[w for _ in range(per) for w in (width, gap)][:-1], hAlign='CENTER')
    t.setStyle(TableStyle(style + [('BOTTOMPADDING', (0, 0), (-1, -2), 18 * mm)]))  # room to sign above the next row
    return [Spacer(1, 20 * mm), KeepTogether([t])]


def blocks(el, look, width=TEXT):
    """The flowables of an element's children: headings, paragraphs, facts, tables, lists, the lines to sign."""
    found = []
    for child in el:
        if not isinstance(child.tag, str) or child.tag in SKIP:
            continue
        kind = classes(child)
        if kind & {'no-print', 'letterhead', 'address', 'dated', 'actions'}:
            continue
        before = px(child, 'margin-top')
        if before:
            found.append(Spacer(1, before * 0.8))
        if child.tag == 'h1':
            text = inline(child, look, look.title.fontSize)
            bar = min(width, max(30 * mm, stringWidth(plain(child), look.title.fontName, look.title.fontSize)))
            found.append(KeepTogether([Bar(bar, look.accent, look.ceremony), Paragraph(text, look.title)]))
        elif child.tag in ('h2', 'h3'):
            found.append(Paragraph(inline(child, look, look.heading.fontSize), look.heading))
        elif child.tag == 'p':
            text = inline(child, look, look.size)
            if not text:
                continue
            only_small = len(child) == 1 and child[0].tag == 'small' and not (child.text or '').strip()
            style = look.small if only_small else look.large if px(child, 'font-size') >= 15 else look.muted if 'muted' in kind else look.body
            paragraph = Paragraph(f'<font color="#c4262e">{text}</font>' if 'fail' in kind else text, style)
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
        elif text := inline(child, look, look.size):
            found.append(Paragraph(text, look.body))
    return found


class Numbered(Canvas):
    """A canvas that holds its pages back to number them, each document on its own: „Seite 2 von 3“."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        totals, seen = Counter(page.get('meta', {}).get('index') for page in self.pages), Counter()
        for page in self.pages:
            self.__dict__.update(page)
            meta = getattr(self, 'meta', {})
            seen[meta.get('index')] += 1
            if totals[meta.get('index')] > 1 and not meta.get('ceremony'):
                self.setFont('PTSans-Regular', 7.5)
                self.setFillColor(MUTED)
                self.drawRightString(WIDTH - RIGHT, FOOT + 3 * mm, meta.get('words', GERMAN)['page'].format(seen[meta.get('index')], totals[meta.get('index')]))
            super().showPage()
        super().save()


class Paper:
    """The pages around a document's text: letterhead, address window, information block, fold marks and footer."""

    def __init__(self, institution):
        self.i = institution
        self.accent = HexColor(institution.accent or '#008a00')
        logo = institution.logo
        self.logo = ImageReader(logo.path) if logo and logo.name.lower().endswith('.png') else None  # an SVG: the wordmark stands in

    def wordmark(self, c, x, top, size=15, centred=False):
        """The Schriftzug, its later lines in small capitals and the accent colour, as on screen; or the logo."""
        if self.logo:
            w, h = self.logo.getSize()
            height = min(17 * mm, 70 * mm * h / w)
            width = height * w / h
            c.drawImage(self.logo, x - width / 2 if centred else x, top - height, width, height, mask='auto')
            return top - height
        y = top
        for n, line in enumerate(self.i.lines):
            if n == 0:
                y -= size
                c.setFont('PTSans-Regular', size)
                c.setFillColor(INK)
                (c.drawCentredString if centred else c.drawString)(x, y, line)
                continue
            y -= size * 1.15
            runs = [(ch.upper(), size * (0.78 if ch.islower() else 1)) for ch in line]
            width = sum(stringWidth(ch, 'PTSans-Regular', s) + size * 0.03 for ch, s in runs)
            cx = x - width / 2 if centred else x
            c.setFillColor(self.accent)
            for ch, s in runs:
                c.setFont('PTSans-Regular', s)
                c.drawString(cx, y, ch)
                cx += stringWidth(ch, 'PTSans-Regular', s) + size * 0.03
        return y

    def footer(self, c, words):
        """Name and address, how to reach the Hochschule, where to pay: three columns under a hairline."""
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(LEFT, FOOT, WIDTH - RIGHT, FOOT)
        payee = self.i.payee if self.i.payee and self.i.payee != self.i.name else words['bank']
        bank = [payee, f'IBAN {self.i.iban}', f'BIC {self.i.bic}' if self.i.bic else ''] if self.i.iban else []
        columns = [[self.i.name, *self.i.address.splitlines()], [self.i.email, self.i.website.removeprefix('https://').removeprefix('http://')], bank]
        x, width = LEFT, TEXT / 3
        for column in columns:
            y = FOOT - 4.2 * mm
            for n, text in enumerate(t for t in column if t):
                c.setFont('PTSans-Bold' if n == 0 and column is columns[0] else 'PTSans-Regular', 7)
                c.setFillColor(INK if n == 0 and column is columns[0] else MUTED)
                c.drawString(x, y, text)
                y -= 3.3 * mm
            x += width

    def later(self, c, meta):
        """The head of a page after the first: the Hochschule and what the document is, small, over a hairline."""
        c.setFont('PTSans-Regular', 7.5)
        c.setFillColor(MUTED)
        c.drawString(LEFT, HEIGHT - 14 * mm, self.i.short_name or self.i.name)
        c.drawRightString(WIDTH - RIGHT, HEIGHT - 14 * mm, meta.get('title', '')[:90])
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(LEFT, HEIGHT - 16 * mm, WIDTH - RIGHT, HEIGHT - 16 * mm)

    def letter(self, c, meta):
        """DIN 5008 Form B: the address in the window, the return line above it, the information block to the right,
        fold and hole marks at the edge."""
        self.wordmark(c, LEFT, HEIGHT - 14 * mm)
        back = ' · '.join([self.i.short_name or self.i.name, *self.i.address.splitlines()])
        c.setFont('PTSans-Regular', 6.5)
        c.setFillColor(MUTED)
        c.drawString(LEFT, HEIGHT - 60 * mm, back[:110])
        c.setStrokeColor(MUTED)
        c.setLineWidth(0.3)
        c.line(LEFT, HEIGHT - 60.8 * mm, LEFT + min(80 * mm, stringWidth(back[:110], 'PTSans-Regular', 6.5)), HEIGHT - 60.8 * mm)
        text = c.beginText(LEFT, HEIGHT - 67.5 * mm)
        text.setFont('PTSans-Regular', 10, 13)
        text.setFillColor(INK)
        for line in meta.get('address', [])[:6]:
            text.textLine(line)
        c.drawText(text)
        x, y = 125 * mm, HEIGHT - 50 * mm
        for n, line in enumerate(meta.get('office', [])):
            c.setFont('PTSans-Bold' if n == 0 else 'PTSans-Regular', 8.5)
            c.setFillColor(INK if n == 0 else MUTED)
            c.drawString(x, y, line)
            y -= 4 * mm
        y -= 3 * mm
        for label, value in meta.get('info', []):
            if value:
                c.setFont('PTSans-Regular', 7)
                c.setFillColor(MUTED)
                c.drawString(x, y, label)
                c.setFont('PTSans-Regular', 9)
                c.setFillColor(INK)
                c.drawString(x + 24 * mm, y, value)
                y -= 4.6 * mm
        c.setStrokeColor(MUTED)
        c.setLineWidth(0.4)
        for at, long in ((105, False), (148.5, True), (210, False)):
            c.line(4 * mm, HEIGHT - at * mm, (10 if long else 7.5) * mm, HEIGHT - at * mm)

    def document(self, c, meta):
        """A Bescheinigung's first page: the wordmark to the left, the office and its address to the right."""
        self.wordmark(c, LEFT, HEIGHT - 14 * mm)
        y = HEIGHT - 17 * mm
        for n, line in enumerate(meta.get('office', [])):
            c.setFont('PTSans-Bold' if n == 0 else 'PTSans-Regular', 8.5)
            c.setFillColor(INK if n == 0 else MUTED)
            c.drawRightString(WIDTH - RIGHT, y, line)
            y -= 4 * mm

    def ceremony(self, c, meta):
        """An Urkunde: a double rule round the page in the accent colour, the wordmark centred."""
        c.setStrokeColor(self.accent)
        c.setLineWidth(1.2)
        c.rect(12 * mm, 12 * mm, WIDTH - 24 * mm, HEIGHT - 24 * mm)
        c.setLineWidth(0.4)
        c.rect(14.5 * mm, 14.5 * mm, WIDTH - 29 * mm, HEIGHT - 29 * mm)
        self.wordmark(c, WIDTH / 2, HEIGHT - 32 * mm, size=17, centred=True)

    def chrome(self, c, doc):
        meta = getattr(c, 'meta', {})
        c.saveState()
        if doc.pageTemplate.id == 'later':
            self.later(c, meta)
        else:
            getattr(self, meta.get('layout', 'document'))(c, meta)
        if meta.get('layout') != 'ceremony':
            self.footer(c, meta.get('words', GERMAN))
        if meta.get('copy'):  # across the page, faint: a copy to read, not the signed and sealed original
            c.translate(WIDTH / 2, HEIGHT / 2)
            c.rotate(55)
            c.setFillColor(INK, alpha=0.05)
            c.setFont('PTSans-Bold', 96)
            c.drawCentredString(0, -32, meta.get('words', GERMAN)['copy'])
        c.restoreState()


def pdf(page, title=''):
    """The `.document`s of a rendered page as one PDF, each from a new page with its own page numbers."""
    institution = Institution.get()
    root = lxml_html.document_fromstring(page)
    paper, story = Paper(institution), []
    layouts = []
    for index, el in enumerate(root.find_class('document')):
        kind = classes(el)
        layout = 'letter' if 'letter' in kind else 'ceremony' if 'ceremony' in kind else 'document'
        look = Look(institution.accent or '#008a00', ceremony=layout == 'ceremony')
        head = next(iter(el.find_class('letterhead')), None)
        office = [line for line in lines(next(iter(head.find_class('muted')), head)) if line not in institution.address.splitlines()] if head is not None and head.find_class('muted') else []
        heading = next((plain(h) for h in el.iter('h1', 'h2')), title)
        meta = {
            'index': index, 'layout': layout, 'office': office, 'title': heading, 'ceremony': layout == 'ceremony', 'copy': 'copy' in kind,
            'words': ENGLISH if el.get('lang') == 'en' else GERMAN,
        }
        if layout == 'letter':
            address, dated = next(iter(el.find_class('address')), None), next(iter(el.find_class('dated')), None)
            meta['address'] = lines(address) if address is not None else []
            meta['office'] = [dated.get('data-office')] if dated is not None and dated.get('data-office') else []
            meta['info'] = [('Aktenzeichen', dated.get('data-reference', '')), ('Datum', dated.get('data-date', ''))] if dated is not None else []
        if story:
            story += [NextPageTemplate(layout), PageBreak()]
        layouts.append(layout)
        story += [Start(meta), Centred(blocks(el, look))] if layout == 'ceremony' else [Start(meta), *blocks(el, look)]
    if not story:
        story = [Paragraph('Kein Dokument.', Look('#008a00').body)]
        layouts = ['document']
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4, title=title or next((m for m in (getattr(s, 'meta', {}).get('title') for s in story) if m), ''),
        author=institution.name, creator='ConCampus', subject=title, lang='de-DE',
    )
    frames = {  # where the text runs: under the letterhead, or under the address window and information block
        'letter': Frame(LEFT, FOOT + 8 * mm, TEXT, HEIGHT - 98 * mm - FOOT - 8 * mm, 0, 0, 0, 0, id='letter'),
        'document': Frame(LEFT, FOOT + 8 * mm, TEXT, HEIGHT - 44 * mm - FOOT - 8 * mm, 0, 0, 0, 0, id='document'),
        'ceremony': Frame(28 * mm, 30 * mm, WIDTH - 56 * mm, HEIGHT - 80 * mm, 0, 0, 0, 0, id='ceremony'),
        'later': Frame(LEFT, FOOT + 8 * mm, TEXT, HEIGHT - 26 * mm - FOOT - 8 * mm, 0, 0, 0, 0, id='later'),
    }
    order = [layouts[0]] + [k for k in frames if k != layouts[0]]
    doc.addPageTemplates([
        PageTemplate(id=k, frames=[frames[k]], onPageEnd=paper.chrome, autoNextPageTemplate=None if k == 'later' else 'later') for k in order
    ])
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
