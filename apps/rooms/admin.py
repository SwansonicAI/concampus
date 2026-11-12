from django.contrib import admin

from .models import Booking, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['name', 'purpose', 'building', 'function', 'capacity', 'equipment']
    list_filter = ['building', 'function', 'institutes']
    search_fields = ['name', 'purpose']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['start', 'end', 'room', 'category', 'label', 'person']
    list_filter = ['category', 'room__building', 'room']
    date_hierarchy = 'start'
    autocomplete_fields = ['person']
    raw_id_fields = ['course', 'participants']
