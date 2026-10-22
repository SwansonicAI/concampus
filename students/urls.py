from django.urls import path

from . import views

app_name = 'students'
urlpatterns = [
    path('', views.index, name='index'),
    path('liste/', views.roster, name='list'),
    path('rueckmeldung/', views.reregistration, name='reregistration'),
    path('bescheide/', views.letters, name='letters'),
    path('nachricht/', views.compose, name='compose'),
    path('<int:pk>/', views.study, name='study'),
    path('<int:pk>/immatrikulieren/', views.enrol, name='enrol'),
    path('<int:pk>/versicherung/', views.insured, name='insured'),
    path('<int:pk>/urlaub/', views.leave, name='leave'),
    path('<int:pk>/exmatrikulieren/', views.exmatriculate, name='exmatriculate'),
    path('<int:pk>/fachwechsel/', views.change, name='change'),
    path('<int:pk>/sperre/', views.hold, name='hold'),
    path('<int:pk>/bescheinigung/', views.certificate, name='certificate'),
]
