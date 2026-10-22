from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Message, Person, Term

admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser  # staff work in the app


@admin.register(Person)
class PersonAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Person', {'fields': ['title', 'given_names', 'birth_name', 'gender', 'birth_date', 'birth_place', 'nationality', 'second_nationality', 'address']}),
        ('Hochschulzugang', {'fields': ['entrance', 'entrance_date', 'entrance_place', 'external_terms']}),
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
    list_display = ['created', 'person', 'subject', 'read']
    search_fields = ['subject', 'person__last_name']
