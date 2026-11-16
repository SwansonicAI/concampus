from django.urls import path

from . import polls, views

app_name = 'rooms'
urlpatterns = [
    path('', views.index, name='index'),
    path('<int:pk>/', views.room, name='room'),
    path('finden/', views.find, name='find'),
    path('meine/', views.mine, name='mine'),
    path('veranstaltungen/', views.events, name='events'),
    path('veranstaltungen/kalender.ics', views.programme, name='programme'),
    path('buchung/<int:pk>/dienst/', views.service, name='service'),
    path('kalender/<str:token>.ics', views.calendar, name='calendar'),
    path('buchen/', views.booking, name='new'),
    path('buchung/<int:pk>/', views.booking, name='booking'),
    path('buchung/<int:pk>/loeschen/', views.delete, name='delete'),
    path('buchung/<int:pk>/absagen/', views.cancel, name='cancel'),
    path('tag-absagen/', views.day_off, name='day_off'),
    path('buchung/<int:pk>/abmelden/', views.absent, name='absent'),
    path('anfragen/', views.requests, name='requests'),
    path('anfragen/<int:pk>/', views.answer, name='answer'),
    path('terminumfragen/', polls.index, name='polls'),
    path('terminumfragen/neu/', polls.new, name='new_poll'),
    path('terminumfragen/<int:pk>/', polls.detail, name='poll'),
    path('terminumfragen/<int:pk>/waehlen/', polls.choose, name='choose'),
    path('terminumfragen/<int:pk>/zeit/', polls.add, name='add_option'),
    path('terminumfragen/<int:pk>/erinnern/', polls.remind, name='remind'),
    path('terminumfragen/<int:pk>/optional/<int:person>/', polls.optional, name='optional'),
    path('terminumfragen/<int:pk>/loeschen/', polls.delete, name='delete_poll'),
]
