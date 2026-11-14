"""Anerkennung of what was achieved elsewhere."""
from decimal import ROUND_DOWN, Decimal

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import number_format

from core import destatis
from core.keys import UNIVERSITIES, KeyChoice, label
from core.access import require
from core.models import Term
from core.templatetags.marks import mark
from core.shortcuts import without_term
from courses.models import Course, Participation
from curriculum.models import Module
from students.models import Document, DocumentType, Study


class RecognitionForm(forms.Form):
    """An Anerkennung in two steps: whose, then which of their open modules, with the grade as it is or converted from
    another grading system by the modified Bavarian formula."""

    who = forms.CharField(label='Studierende:r', widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}))
    module = forms.ChoiceField(label='Modul', help_text='Ein Teil, oder das ganze Modul mit allen noch offenen Teilen.')
    grade = forms.DecimalField(label='Note', required=False, min_value=1, max_value=4, decimal_places=1, localize=True, help_text='Leer: bestanden ohne Note.')
    foreign = forms.DecimalField(label='Oder umrechnen: erzielte Note', required=False, decimal_places=2, localize=True)
    best = forms.DecimalField(label='Beste Note dort', required=False, decimal_places=2, localize=True)
    worst = forms.DecimalField(label='Schlechteste bestandene Note dort', required=False, decimal_places=2, localize=True, help_text='Modifizierte bayerische Formel.')
    origin = KeyChoice(keys=UNIVERSITIES, label='Hochschule', help_text='Im Ausland: das Land.')
    place = forms.CharField(label='Name der Hochschule', required=False, help_text='Bei einer Hochschule im Ausland, z. B. „mdw Wien“.')
    taken = forms.CharField(label='Semester dort', required=False, widget=forms.TextInput(attrs={'placeholder': 'z. B. SoSe 2024'}))
    credits = forms.DecimalField(label='ECTS dort', required=False, min_value=0, decimal_places=1, localize=True)
    proof = forms.FileField(label='Nachweis', required=False, help_text='Transcript oder Bescheinigung; kommt in die Studienakte.')
    FIELDS = ['module', 'grade', 'foreign', 'best', 'worst', 'origin', 'place', 'taken', 'credits', 'proof']
    layout = [(None, ['who', 'module', 'grade', ('foreign', 'best', 'worst'), ('origin', 'place'), ('taken', 'credits'), 'proof'])]

    def __init__(self, *args, study=None, **kwargs):
        super().__init__(*args, **kwargs)
        if study is None:
            for name in self.FIELDS:
                del self.fields[name]
            return
        del self.fields['who']
        passed = Participation.objects.filter(study=study, passed=True, module__isnull=False).values('module')
        self.open = {str(m.pk): m for m in Module.objects.filter(program=study.program, children=None).exclude(pk__in=passed).select_related('parent').order_by('code')}
        groups = {}
        for m in self.open.values():
            groups.setdefault(m.parent, []).append(m)
        self.fields['module'].choices = [('', '–')] + [
            (f'{parent.code} {parent.title}' if parent else 'Weitere', ([(f'm{parent.pk}', 'Ganzes Modul')] if parent and len(parts) > 1 else []) + [(str(m.pk), f'{m.code} {m.title}') for m in parts])
            for parent, parts in groups.items()
        ]

    def clean_who(self):
        found = Study.find(self.cleaned_data['who'])
        if len(found) != 1:
            raise forms.ValidationError(f'Mehrere passen: {", ".join(str(s.person) for s in found)}.' if found else 'Niemand Immatrikuliertes passt dazu.')
        return found[0]

    def clean_module(self):
        value = self.cleaned_data['module']
        return [m for m in self.open.values() if f'm{m.parent_id}' == value] if value.startswith('m') else [self.open[value]]

    def clean(self):
        data = super().clean()
        given = [data.get(name) for name in ('foreign', 'best', 'worst')]
        if 'module' in self.fields and any(v is not None for v in given):
            foreign, best, worst = given
            if data.get('grade') is not None:
                raise forms.ValidationError('Entweder eine Note eintragen oder umrechnen, nicht beides.')
            if None in given or best == worst:
                raise forms.ValidationError('Zum Umrechnen erzielte, beste und schlechteste bestandene Note angeben.')
            grade = 1 + 3 * (best - foreign) / (best - worst)  # 1,0 for the best, 4,0 for the worst passing grade
            data['grade'] = max(Decimal('1.0'), min(Decimal('4.0'), grade.quantize(Decimal('0.1'), ROUND_DOWN)))
        if data.get('origin'):  # kept as one line of the record: Hochschule, Land, Semester, ECTS
            name = label(UNIVERSITIES, data['origin'])
            abroad = data['origin'].startswith(destatis.ABROAD)
            credits = data.get('credits') is not None and f'{number_format(data["credits"])} ECTS'
            data['note'] = ', '.join(filter(None, [data.get('place') if abroad else name, name if abroad else '', data.get('taken'), credits]))
        if (proof := data.get('proof')) and (refusal := DocumentType.system('anerkennung').refuses(proof)):
            self.add_error('proof', refusal)
        return data


@require('recognise')
def recognition(request):
    """Anerkennung: from the study file, or by name or Matrikelnummer first."""
    term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester, in dem Anerkennungen verbucht werden')
    course, _ = Course.objects.get_or_create(term=term, kind=Course.Kind.RECOGNITION, defaults={'title': f'Anerkennungen {term}'})
    study, pk = None, request.GET.get('studium', '')
    if pk:
        studies = request.user.within(Study.objects.select_related('person', 'program', 'subject'), 'recognise')
        study = get_object_or_404(studies, pk=pk if pk.isdigit() else 0, status=Study.Status.ENROLLED)
    form = RecognitionForm(request.POST or None, request.FILES or None, study=study, initial=request.GET.dict())  # filled in from an Auslandsaufenthalt
    if form.is_valid() and study is None:
        if request.user.may('recognise', found := form.cleaned_data['who']):
            return redirect(f'{reverse("exams:recognition")}?studium={found.pk}')
        form.add_error(None, 'Diese Person studiert außerhalb der Institute und Studiengänge, für die Ihr Amt zuständig ist.')
    elif form.is_valid():
        data = form.cleaned_data
        for module in data['module']:
            Participation.objects.update_or_create(course=course, study=study, module=module, defaults={'grade': data['grade'], 'passed': True, 'note': data['note']})
        codes = ', '.join(m.code for m in data['module'])
        if data['proof']:
            Document.hand_in(study, DocumentType.system('anerkennung'), data['proof'])
        study.notify('anerkennung', reverse('exams:transcript', args=[study.pk]), modul=codes, herkunft=data['note'])
        messages.success(request, f'{codes} für {study.person} anerkannt' + (f' mit {mark(data["grade"])}.' if data['grade'] is not None else '.'))
        return redirect(f'{reverse("exams:recognition")}?studium={study.pk}')
    return render(request, 'exams/recognition.html', {
        'form': form, 'course': course, 'study': study,
        'recent': course.participations.filter(study__in=request.user.within(Study.objects.all(), 'recognise')).select_related('study__person', 'module').order_by('-created')[:20],
    })
