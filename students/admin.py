from django.contrib import admin

from .models import Document, Study, StudyTerm


class StudyTermInline(admin.TabularInline):
    model = StudyTerm
    extra = 0


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    list_display = ['person', 'program', 'subject', 'start_term', 'status', 'since', 'teacher']
    list_filter = ['status', 'program', 'subject', 'start_term']
    search_fields = ['person__first_name', 'person__last_name', 'person__matriculation']
    autocomplete_fields = ['person', 'teacher']
    raw_id_fields = ['focus']
    inlines = [StudyTermInline]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'person', 'study', 'created']
    search_fields = ['title', 'person__last_name']
    autocomplete_fields = ['person']
    raw_id_fields = ['study']
