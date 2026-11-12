from django.urls import path

from .views import applicant, board, juries

app_name = 'admissions'
urlpatterns = [
    path('', board.index, name='index'),
    path('bewerbungen/', board.board, name='board'),
    path('kommissionen/', juries.juries, name='juries'),
    path('konto/', applicant.signup, name='signup'),
    path('bewerben/', applicant.apply, name='apply'),
    path('meine/', applicant.mine, name='mine'),
    path('meine/<int:pk>/annahme/', applicant.accept, name='accept'),
    path('meine/<int:pk>/hochschulzugang/', applicant.entrance, name='entrance'),
    path('meine/<int:pk>/angaben/', applicant.details, name='details'),
    path('meine/<int:pk>/zurueckziehen/', applicant.retract, name='retract'),
    path('bewerbung/<int:pk>/', board.application, name='application'),
    path('bewerbung/<int:pk>/entscheidung/', board.decide, name='decide'),
    path('bewerbungen/entscheiden/', board.decide_many, name='decide_many'),
    path('bewerbungen/erinnern/', board.remind, name='remind'),
    path('bewerbung/<int:pk>/unterlagen/', applicant.upload, name='upload'),
    path('bewerbung/<int:pk>/video/', applicant.reuse, name='reuse'),
    path('unterlagen/<int:pk>/', applicant.document, name='document'),
    path('unterlagen/<int:pk>/entfernen/', applicant.discard, name='discard'),
    path('termin/<int:pk>/kalender.ics', applicant.calendar, name='calendar'),
    path('bewerbung/<int:pk>/vordruck/<str:kind>/', board.blank, name='blank'),
    path('pruefung/<int:pk>/', juries.audition, name='audition'),
    path('pruefung/<int:pk>/sichtung/', juries.review, name='review'),
    path('pruefung/<int:pk>/termine/', juries.plan, name='plan'),
    path('pruefung/<int:pk>/verlegen/', juries.move, name='move'),
    path('pruefung/<int:pk>/weiter/', juries.advance, name='advance'),
    path('pruefung/<int:pk>/abschluss/', juries.close, name='close'),
    path('pruefung/<int:pk>/nachruecken/', juries.move_up, name='move_up'),
]
