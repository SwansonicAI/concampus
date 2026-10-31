from django import forms

from .models import Module


class Graded(forms.ModelForm):
    """A Prüfungsordnung in use: grades and Zeugnisse are reckoned from it, not stored, so a change to what they are
    reckoned from (`graded`) changes them at once. Such fields say so; a change to them is shown before and after and
    saved only with its reason, which the Verlauf keeps."""

    graded = ()

    def guard(self, program):
        self.enrolled, self.graduated = program.affected() if program.pk else (0, 0)
        if not (self.instance.pk and (self.enrolled or self.graduated)):
            return
        self.note = (
            f'In Gebrauch: {self.enrolled} Studierende, {self.graduated} Absolvent:innen. Was auf Noten wirkt, ist markiert und '
            'braucht einen Grund; ein neuer Studienplan ist eine neue PO-Version („Als neue PO-Version kopieren“ beim Studiengang).'
        )
        for name in self.graded:
            if name in self.fields:
                self.fields[name].help_text = ' '.join(filter(None, [str(self.fields[name].help_text), 'Wirkt auf Noten.']))
        self.fields['reason'] = forms.CharField(
            label='Grund der Änderung', max_length=200, required=False, help_text='Nötig, wenn sich Noten ändern; der Verlauf hält ihn fest.',
        )

    def shown(self, name, value):
        field = self.fields[name]
        if hasattr(field, 'queryset'):
            value = field.queryset.model.objects.filter(pk=getattr(value, 'pk', value)).first() if value else None
        elif getattr(field, 'choices', None):
            value = dict(field.choices).get(value, value)
        return '–' if value in (None, '') else str(value)

    def clean(self):
        data = super().clean()
        changed = [name for name in self.graded if name in self.changed_data]
        if changed and 'reason' in self.fields and not data.get('reason'):
            what = '; '.join(f'{self.fields[n].label}: {self.shown(n, self.initial.get(n))} → {self.shown(n, data.get(n))}' for n in changed)
            raise forms.ValidationError(
                f'{what}. Das ändert die Noten von {self.enrolled} Studierenden und die Zeugnisse von {self.graduated} '
                'Absolvent:innen. Bitte den Grund angeben und erneut speichern.'
            )
        return data


class ProgramForm(Graded):
    graded = ('attempts', 'rounding', 'mean_rounding', 'honours')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.guard(self.instance)


class ModuleForm(Graded):
    graded = ('parent', 'credits', 'exam', 'final', 'weight')

    class Meta:
        model = Module
        fields = ['parent', 'code', 'title', 'credits', 'semester', 'exam', 'final', 'weight', 'description', 'requires', 'waived']
        widgets = {'description': forms.Textarea(attrs={'rows': 3}), 'requires': forms.CheckboxSelectMultiple}

    layout = [(None, ['parent', 'title', ('code', 'credits', 'semester', 'weight'), ('exam', 'final'), 'description', 'requires', 'waived'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        program = self.instance.program
        self.guard(program)
        modules = list(program.modules.all())
        below = self.instance.within() if self.instance.pk else set()  # neither the module itself nor anything under it can hold it
        holding = {m.parent_id for m in modules}
        depth, order = {}, []

        def walk(parent, level):  # the plan's tree, each under its parent
            for m in sorted((m for m in modules if m.parent_id == parent), key=lambda m: (m.semester or 0, m.code, m.title)):
                depth[m.pk] = level
                order.append(m)
                walk(m.pk, level + 1)

        walk(None, 0)
        # what can hold a module: what holds some already, or an area or module of no semester; never course work or an exam
        fit = [m for m in order if m.pk not in below and (m.pk in holding or not (m.exam or m.semester))]
        parent = self.fields['parent']
        parent.queryset = program.modules.filter(pk__in=[m.pk for m in fit])
        parent.choices = [('', 'Oberste Ebene')] + [(m.pk, ' ' * depth[m.pk] + f'{m.code} {m.title}'.strip()) for m in fit]
        self.fields['waived'].queryset = program.subjects.all()  # the Hauptfächer of this Studiengang
        earlier = [m for m in order if m.pk != self.instance.pk and not m.exam and m.pk not in holding]  # course work of the plan
        self.fields['requires'].queryset = program.modules.filter(pk__in=[m.pk for m in earlier])
        self.fields['requires'].choices = [(m.pk, f'{m.code} {m.title}'.strip() + (f' · {m.semester}. Semester' if m.semester else '')) for m in earlier]
