from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Message, Person, Term

admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser  # staff work in the app


@admin.register(Person)
class PersonAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Person', {'fields': ['title', 'given_names', 'birth_name', 'gender', 'birth_date', 'birth_place', 'nationality', 'second_nationality', 'address', 'home', 'residence']}),
        ('Hochschulzugang', {'fields': ['entrance', 'entrance_date', 'entrance_region', 'first_university', 'first_term', 'external_terms']}),
        ('Letzter Abschluss', {'fields': ['degree_university', 'degree_exam', 'degree_field', 'degree_date', 'degree_grade', 'vocational', 'internship']}),
        ('Krankenversicherung', {'fields': ['insurer', 'insurance', 'insurance_confirmed']}),
        ('Hochschule', {'fields': ['matriculation', 'hold']}),
    )
    list_display = ['username', 'first_name', 'last_name', 'email', 'matriculation', 'hold']
    search_fields = ['username', 'first_name', 'last_name', 'birth_name', 'email', 'matriculation']


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ['name', 'start', 'end', 'lectures_start', 'lectures_end', 'deadline', 'fee']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Sent is sent: Bescheide stay as they went out."""

    list_display = ['created', 'person', 'subject', 'read']
    search_fields = ['subject', 'person__last_name']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
