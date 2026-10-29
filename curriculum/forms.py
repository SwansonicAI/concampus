from django import forms

from .models import Module


class ModuleForm(forms.ModelForm):
    class Meta:
        model = Module
        fields = ['parent', 'code', 'title', 'credits', 'semester', 'exam', 'final', 'weight', 'description', 'waived']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    layout = [(None, ['parent', 'title', ('code', 'credits', 'semester', 'weight'), ('exam', 'final'), 'description', 'waived'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        modules = list(self.instance.program.modules.all())
        below, frontier = set(), {self.instance.pk} - {None}
        while frontier:  # neither the module itself nor anything under it can hold it
            below |= frontier
            frontier = {m.pk for m in modules if m.parent_id in frontier} - below
        parent = self.fields['parent']
        parent.queryset = self.instance.program.modules.exclude(pk__in=below)
        parent.label_from_instance = lambda m: f'{m.code} {m.title}'.strip()
        parent.empty_label = 'Oberste Ebene'
