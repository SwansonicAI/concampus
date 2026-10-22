from django.contrib import admin

from .models import Course, Participation, Score


class ParticipationInline(admin.TabularInline):
    model = Participation
    fields = ['study', 'module', 'slot', 'grade', 'passed', 'note']
    raw_id_fields = ['study', 'module']
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ['title', 'kind', 'term', 'subject', 'hours', 'capacity', 'released']
    list_filter = ['kind', 'term', 'subject']
    search_fields = ['title']
    filter_horizontal = ['modules', 'staff']
    inlines = [ParticipationInline]


@admin.register(Participation)
class ParticipationAdmin(admin.ModelAdmin):
    list_display = ['study', 'course', 'module', 'grade', 'passed']
    list_filter = ['course__kind', 'course__term']
    raw_id_fields = ['study', 'course', 'module']


admin.site.register(Score)
