from django.urls import path

from .views import archive, documents, enrolment, lists, reregistration, study

app_name = 'students'
urlpatterns = [
    path('', lists.index, name='index'),
    path('liste/', lists.roster, name='list'),
    path('rueckmeldung/', reregistration.reregistration, name='reregistration'),
    path('bescheide/', lists.letters, name='letters'),
    path('versandt/', documents.notices, name='notices'),
    path('versandt/gedruckt/', documents.printed, name='printed'),
    path('pruefen/', documents.verify, name='verify'),
    path('nachricht/', lists.compose, name='compose'),
    path('vorschlaege/', lists.suggest, name='suggest'),
    path('aussonderung/', archive.disposal, name='disposal'),
    path('krankenkassen/', reregistration.insurers, name='insurers'),
    path('aussonderung/<int:pk>/akte/', archive.handover, name='handover'),
    path('<int:pk>/', study.study, name='study'),
    path('<int:pk>/verlauf/', study.study, {'page': 'students/progress.html'}, name='progress'),
    path('<int:pk>/immatrikulieren/', enrolment.enrol, name='enrol'),
    path('immatrikulieren/', enrolment.enrol_many, name='enrol_many'),
    path('gasthoerende/', enrolment.guest, name='guest'),
    path('<int:pk>/hochschulzugang/', enrolment.checked, name='checked'),
    path('<int:pk>/beitrag/', reregistration.fee_paid, name='fee_paid'),
    path('rueckmeldung/<int:pk>/zuruecknehmen/', reregistration.unpay, name='unpay'),
    path('<int:pk>/urlaub/', enrolment.leave, name='leave'),
    path('<int:pk>/teilzeit/', enrolment.part_time, name='part_time'),
    path('<int:pk>/gebuehren/', enrolment.waive, name='waive'),
    path('<int:pk>/schwerpunkt/', study.focus, name='focus'),
    path('<int:pk>/empfehlung/', study.advise, name='advise'),
    path('<int:pk>/exmatrikulieren/', enrolment.exmatriculate, name='exmatriculate'),
    path('<int:pk>/fachwechsel/', study.change, name='change'),
    path('<int:pk>/sperre/', study.hold, name='hold'),
    path('<int:pk>/einschraenkung/', study.restrict, name='restrict'),
    path('<int:pk>/bescheinigung/', documents.certificate, name='certificate'),
    path('<int:pk>/exmatrikulation/', documents.exmatriculation, name='exmatriculation'),
    path('<int:pk>/studienzeiten/', documents.periods, name='periods'),
]
