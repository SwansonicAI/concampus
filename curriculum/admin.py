from django.contrib import admin

from .models import Module, Program, Subject


class ModuleInline(admin.TabularInline):
    model = Module
    fields = ['code', 'title', 'credits', 'semester', 'parent']
    extra = 0


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'semesters']
    filter_horizontal = ['subjects']
    inlines = [ModuleInline]


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ['code', 'title', 'program', 'credits', 'semester', 'parent']
    list_filter = ['program']
    search_fields = ['code', 'title']


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'institute']
