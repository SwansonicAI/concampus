from django.urls import path

from . import views

app_name = 'students'
urlpatterns = [
    path('', views.index, name='index'),
    path('liste/', views.roster, name='list'),
    path('rueckmeldung/', views.reregistration, name='reregistration'),
    path('bescheide/', views.letters, name='letters'),
    path('versandt/', views.notices, name='notices'),
    path('pruefen/', views.verify, name='verify'),
    path('nachricht/', views.compose, name='compose'),
    path('vorschlaege/', views.suggest, name='suggest'),
    path('aussonderung/', views.disposal, name='disposal'),
    path('krankenkassen/', views.insurers, name='insurers'),
    path('aussonderung/<int:pk>/akte/', views.handover, name='handover'),
    path('<int:pk>/', views.study, name='study'),
    path('<int:pk>/immatrikulieren/', views.enrol, name='enrol'),
    path('immatrikulieren/', views.enrol_many, name='enrol_many'),
    path('<int:pk>/hochschulzugang/', views.checked, name='checked'),
    path('<int:pk>/beitrag/', views.fee_paid, name='fee_paid'),
    path('rueckmeldung/<int:pk>/zuruecknehmen/', views.unpay, name='unpay'),
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
