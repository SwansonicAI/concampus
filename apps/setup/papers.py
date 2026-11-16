"""Briefbögen: what letters, Bescheinigungen and Urkunden show around their text, laid out on the page by hand
(core.paper, core/static/core/paper.js), with the pictures and fonts they use; and which document is set on which."""
import json

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.csp import CSP
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_POST

from core import paper
from core.access import require
from core.models import Artwork, Institution, Layout

CONTENT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.ttf': 'font/ttf'}


def uses(key, found=None):
    """The documents set on a Briefbogen: [(document, what it is)]."""
    return [(doc, paper.DOCUMENTS[doc][0]) for doc, chosen in paper.chosen(found=found).items() if chosen == key]


def carries_letters(key, found=None):
    return any(doc in paper.LETTERS for doc, _ in uses(key, found))


def sample_for(key, design, found=None):
    """The sample a Briefbogen is best tried on: a letter where it carries letters, an Urkunde where its text stands in the middle."""
    return 'brief' if carries_letters(key, found) else 'urkunde' if design['style'].get('middle') else 'bescheinigung'


@require('manage_institution')
def papers(request):
    """The Briefbögen, which document each carries, a new one from any of them."""
    found = paper.layouts()
    institution = Institution.get()
    if request.POST.get('art') == 'neu':
        name, base = request.POST.get('name', '').strip()[:80], request.POST.get('vorlage')
        if not name or base not in found:
            messages.error(request, 'Bitte einen Namen und eine Vorlage angeben.')
            return redirect('setup:papers')
        key, n = slugify(name)[:36] or 'briefbogen', 1
        while key in found or key in paper.STANDARD or key == 'dateien':  # the uploads' address (setup/urls.py)
            n += 1
            key = f'{slugify(name)[:33] or "briefbogen"}-{n}'
        Layout.objects.create(key=key, name=name, design=found[base][1])
        return redirect('setup:paper', key)
    if request.POST.get('art') == 'zuordnung':
        papers, wrong = {}, []
        for doc, (title, standard) in paper.DOCUMENTS.items():
            key = request.POST.get(doc)
            if key not in found:
                continue
            if doc in paper.LETTERS and not any(el['type'] == 'address' for el in found[key][1]['elements']):
                wrong.append(f'{title}: „{found[key][0]}“ hat kein Anschriftfeld')
            elif key != standard:
                papers[doc] = key
        if wrong:
            messages.error(request, 'Nicht gespeichert. ' + '; '.join(wrong) + '.')
        else:
            institution.papers = papers
            institution.save(update_fields=['papers'])
            messages.success(request, 'Gespeichert.')
        return redirect('setup:papers')
    chosen, changed = paper.chosen(institution, found), set(Layout.objects.filter(key__in=paper.STANDARD).values_list('key', flat=True))
    return render(request, 'setup/papers.html', {
        'layouts': [
            (key, name, standard, standard and key in changed, uses(key, found), sample_for(key, design, found)) for key, (name, design, standard) in found.items()
        ],
        'documents': [(doc, title, chosen[doc]) for doc, (title, _) in paper.DOCUMENTS.items()],
        'choices': [(key, name) for key, (name, _, _) in found.items()],
    })


@require('manage_institution')
def editor(request, key):
    """One Briefbogen, laid out on the page: saved, set back to its standard, or deleted."""
    found = paper.layouts()
    if key not in found:
        raise Http404
    name, design, standard = found[key]
    record = Layout.objects.filter(key=key).first()
    letters = carries_letters(key, found)
    if request.POST.get('art') == 'standard' and standard:
        if record:
            record.delete()
        messages.success(request, f'„{paper.STANDARD[key][0]}“ steht wieder auf dem Standard.')
        return redirect('setup:paper', key)
    if request.POST.get('art') == 'loeschen' and record and not standard:
        institution = Institution.get()
        institution.papers = {doc: k for doc, k in (institution.papers or {}).items() if k != key}
        institution.save(update_fields=['papers'])
        record.delete()
        messages.success(request, f'„{record.name}“ gelöscht; was darauf stand, steht wieder auf seinem Standard.')
        return redirect('setup:papers')
    if request.method == 'POST':
        posted, name = request.POST.get('design', ''), request.POST.get('name', '').strip()[:80] or name
        try:
            design = json.loads(posted)
            cleaned = paper.clean(design, letter=letters)
        except (ValueError, ValidationError) as error:
            messages.error(request, 'Nicht gespeichert: ' + ' '.join(getattr(error, 'messages', ['Der Briefbogen ließ sich nicht lesen.'])))
            if not (isinstance(design, dict) and isinstance(design.get('style'), dict) and isinstance(design.get('elements'), list)):
                design = found[key][1]
        else:
            record = record or Layout(key=key)
            record.name, record.design = name, cleaned
            record.save()
            messages.success(request, f'„{name}“ gespeichert.')
            return redirect('setup:paper', key)
    return render(request, 'setup/paper.html', {
        'menu': None,  # the window's width for the page and the rail (setup/paper.html)
        'key': key, 'name': name, 'standard': standard, 'own': bool(record), 'record': record, 'used': uses(key, found),
        'letters': letters, 'sample': sample_for(key, design, found), 'samples': paper.SAMPLES,
        'editor': editor_data(design, name) | {
            'letters': letters, 'used': [title for _, title in uses(key, found)], 'assign': reverse('setup:papers'),
            'unsaved': request.method == 'POST',  # a save refused: what was sent stands, still to be saved
        },
    })


def editor_data(design, name):
    """What the page in the browser needs to lay the Briefbogen out as the PDF will (paper.js)."""
    institution = Institution.get()
    own = paper.own_fonts()
    fonts = {key: {'name': title, 'faces': [{'w': w, 'i': i, 'url': static(f'core/fonts/{stem}-{suffix}.ttf')} for (w, i), suffix in paper.FACES.items()]}
             for key, (title, stem) in paper.FAMILIES.items()}
    fonts |= {key: {'name': title, 'own': True, 'faces': [face(art) for art in arts.values()]} for key, (title, arts) in own.items()}
    pictures = [{'pk': a.pk, 'name': a.name, 'url': reverse('setup:artwork', args=[a.pk]), 'w': a.width, 'h': a.height} for a in Artwork.objects.filter(kind=Artwork.Kind.PICTURE)]
    logo = institution.logo
    sample = paper.values(institution, {  # the Hochschule's own values; for a document, its person and its pages, an example
        'words': paper.GERMAN, 'office': ['Akademische und Studentische Angelegenheiten'], 'title': 'Zulassung zum Studium', 'reference': '2026-1234',
        'date': '16. November 2026', 'person': 'Erika Mustermann', 'matrikel': '100123',
    }) | {'seitenzahl': 'Seite 1 von 2', 'seite': '1', 'seiten': '2'}
    return {
        'design': design, 'name': name, 'fonts': fonts, 'pictures': pictures, 'values': sample,
        'fields': [{'key': key, 'group': group, 'label': label, 'about': about, 'example': group not in (paper.HOCHSCHULE, paper.BANK)}
                   for key, (group, label, about) in paper.FIELDS.items()],
        'accent': institution.accent or '#008a00', 'wordmark': institution.lines,
        'logo': reverse('logo') if logo else '', 'svg': bool(logo) and logo.name.lower().endswith('.svg'),
        'upload': reverse('setup:artworks'),
    }


@require('manage_institution')
@xframe_options_sameorigin
@csp_override(settings.SECURE_CSP | {'frame-ancestors': [CSP.SELF]})  # shown in the editor's own frame, nowhere else
def preview(request, key):
    """The sample document (paper.sample) on a Briefbogen: as posted from the editor, unsaved, or as kept."""
    kind = request.POST.get('muster') or request.GET.get('muster')
    kind = kind if kind in paper.SAMPLES else 'brief'
    if request.method == 'POST':
        try:
            design = paper.clean(json.loads(request.POST.get('design', '')))
        except (ValueError, ValidationError) as error:
            return render(request, 'setup/paper_error.html', {'errors': getattr(error, 'messages', ['Der Briefbogen ließ sich nicht lesen.'])}, status=400)
    else:
        found = paper.layouts()
        if key not in found:
            raise Http404
        design = found[key][1]
    return paper.respond(paper.pdf(paper.sample(kind), f'Muster {paper.SAMPLES[kind]}', design=design), f'Briefbogen {paper.SAMPLES[kind]}')


def face(art):
    """A font face as the editor knows it (paper.js)."""
    return {'pk': art.pk, 'w': art.weight or 400, 'i': art.italic, 'url': reverse('setup:artwork', args=[art.pk])}


@require('manage_institution')
@require_POST
def artworks(request):
    """Uploads from the editor, checked and kept. A picture (paper.accept); or the files of a font family, as many as
    it has, once whoever uploads confirms its licence allows embedding it in PDF documents: each face found in them made
    TrueType (paper.accept_font), one already there of the same family, weight and slant replaced. JSON for the editor:
    what is kept, and what was not, saying why."""
    kind, uploads = request.POST.get('art'), request.FILES.getlist('datei')
    if kind not in Artwork.Kind.values or not uploads:
        return JsonResponse({'error': 'Bitte eine Datei wählen.'}, status=400)
    if kind == Artwork.Kind.PICTURE:
        try:
            file, width, height = paper.accept(uploads[0])
        except ValidationError as error:
            return JsonResponse({'error': ' '.join(error.messages)}, status=400)
        art = Artwork.objects.create(kind=kind, name=uploads[0].name.rsplit('.', 1)[0][:80], file=file, width=width, height=height)
        return JsonResponse({'pk': art.pk, 'name': art.name, 'url': reverse('setup:artwork', args=[art.pk]), 'w': width, 'h': height})
    if not request.POST.get('lizenz'):
        return JsonResponse({'error': 'Bitte bestätigen, dass die Lizenz der Schrift das Einbetten in PDF-Dokumente erlaubt.'}, status=400)
    if len(uploads) > 24:
        return JsonResponse({'error': 'Höchstens 24 Dateien auf einmal.'}, status=400)
    kept, refused = [], []
    for upload in uploads:
        try:
            found = paper.accept_font(upload)
        except ValidationError as error:
            refused.append(f'{upload.name}: {" ".join(error.messages)}')
            continue
        for file, family, weight, italic in found:
            family = next((a.name for a in Artwork.objects.filter(kind=kind) if slugify(a.name) == slugify(family)), family)  # one family, however written
            # a new record, not the old one changed: a face is set by its record's number (paper.faces)
            Artwork.objects.filter(kind=kind, name=family, weight=weight, italic=italic).delete()
            art = Artwork.objects.create(kind=kind, name=family, weight=weight, italic=italic, licensed=True, file=file)
            kept.append({'key': paper.OWN + slugify(family), 'name': family} | face(art))
    return JsonResponse({'kept': kept, 'refused': refused}, status=200 if kept else 400)


@require('manage_institution')
def artwork(request, pk):
    """A picture or font as kept, for the editor only: a signature scanned for an Urkunde is nobody else's business."""
    art = get_object_or_404(Artwork, pk=pk)
    response = FileResponse(art.file.open('rb'), content_type=CONTENT.get(art.file.name[-4:].lower(), 'application/octet-stream'))
    response['Content-Security-Policy'] = 'sandbox'
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@require('manage_institution')
@require_POST
def artwork_delete(request, pk):
    """A picture or font face gone, unless a Briefbogen sets it."""
    art = get_object_or_404(Artwork, pk=pk)
    family = paper.OWN + slugify(art.name)
    for name, design, _ in paper.layouts().values():
        style = design.get('style', {})
        if any(el.get('artwork') == art.pk for el in design.get('elements', [])) if art.kind == Artwork.Kind.PICTURE else (
            family in (style.get('font'), style.get('heading')) or any(el.get('font') == family for el in design.get('elements', []))
        ):
            return JsonResponse({'error': f'„{name}“ verwendet es noch.'}, status=400)
    art.delete()
    return JsonResponse({'ok': True})
