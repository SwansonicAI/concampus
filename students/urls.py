from django.urls import path

from . import views

app_name = 'students'
urlpatterns = [
    path('', views.index, name='index'),
    path('liste/', views.roster, name='list'),
    path('rueckmeldung/', views.reregistration, name='reregistration'),
    path('bescheide/', views.letters, name='letters'),
    path('nachricht/', views.compose, name='compose'),
    path('vorschlaege/', views.suggest, name='suggest'),
    path('<int:pk>/', views.study, name='study'),
    path('<int:pk>/immatrikulieren/', views.enrol, name='enrol'),
    path('immatrikulieren/', views.enrol_many, name='enrol_many'),
    path('<int:pk>/versicherung/', views.insured, name='insured'),
    path('<int:pk>/beitrag/', views.fee_paid, name='fee_paid'),
    path('<int:pk>/urlaub/', views.leave, name='leave'),
    path('<int:pk>/schwerpunkt/', views.focus, name='focus'),
    path('<int:pk>/empfehlung/', views.advise, name='advise'),
    path('<int:pk>/exmatrikulieren/', views.exmatriculate, name='exmatriculate'),
    path('<int:pk>/fachwechsel/', views.change, name='change'),
    path('<int:pk>/sperre/', views.hold, name='hold'),
    path('<int:pk>/bescheinigung/', views.certificate, name='certificate'),
    path('<int:pk>/exmatrikulation/', views.exmatriculation, name='exmatriculation'),
    path('<int:pk>/studienzeiten/', views.periods, name='periods'),
]
