from django.urls import path

from . import views

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
]
