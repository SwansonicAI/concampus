"""Incoming payments as the bank lists them, to book the Semesterbeitrag by the Matrikelnummer in the Verwendungszweck."""
import re
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree

DAY = re.compile(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b')
AMOUNT = re.compile(r'(?<![\d.,])(-?\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,])')  # German notation: 1.341,30
NUMBER = re.compile(r'\b\d{5,12}\b')


def credits(data):
    """(day, amount, text) of each incoming payment: from a CAMT.053 statement, or from a CSV export, one booking a line
    with its date first and its amount in German notation."""
    text = data.decode('utf-8-sig', errors='replace')
    if text.lstrip().startswith('<'):
        root = ElementTree.fromstring(text)
        for element in root.iter():
            element.tag = element.tag.rpartition('}')[2]  # namespaces differ between CAMT versions
        for entry in root.iter('Ntry'):
            if entry.findtext('CdtDbtInd') == 'CRDT':
                booked = entry.findtext('BookgDt/Dt') or entry.findtext('BookgDt/DtTm') or entry.findtext('ValDt/Dt')
                yield date.fromisoformat(booked[:10]), Decimal(entry.findtext('Amt')), ' '.join(u.text or '' for u in entry.iter('Ustrd'))
        return
    for line in text.splitlines():
        day, amount = DAY.search(line), AMOUNT.search(line)
        if day and amount and not amount.group(1).startswith('-'):
            yield date(*map(int, reversed(day.groups()))), Decimal(amount.group(1).replace('.', '').replace(',', '.')), line


def numbers(text):
    """The digits in a Verwendungszweck that could be a Matrikelnummer."""
    return NUMBER.findall(text)
