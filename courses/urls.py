from django.urls import path

from . import views

app_name = 'courses'
urlpatterns = [
    path('', views.index, name='index'),
    path('verzeichnis/', views.catalogue, name='catalogue'),
    path('meine/', views.mine, name='mine'),
    path('stundenplan/', views.timetable, name='timetable'),
    path('deputat/', views.load, name='load'),
    path('semesterwechsel/', views.rollover, name='rollover'),
    path('neu/', views.edit, name='new'),
    path('<int:pk>/', views.course, name='course'),
    path('<int:pk>/bearbeiten/', views.edit, name='edit'),
    path('<int:pk>/hinzufuegen/', views.add, name='add'),
    path('<int:pk>/anmelden/', views.register, name='register'),
    path('<int:pk>/planen/', views.plan, name='plan'),
    path('<int:pk>/unterrichtszeiten/', views.lessons, name='lessons'),
    path('teilnahme/<int:pk>/abmelden/', views.withdraw, name='withdraw'),
    path('<int:pk>/termin/', views.date, name='date'),
    path('<int:pk>/ensemble/', views.ensemble, name='ensemble'),
    path('ensemble/<int:pk>/', views.coach, name='coach'),
    path('anfrage/<int:pk>/', views.choose, name='choose'),
    path('anfrage/<int:pk>/zurueckziehen/', views.retract, name='retract'),
]
