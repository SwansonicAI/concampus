from django.urls import path

from . import views

app_name = 'admissions'
urlpatterns = [
    path('', views.index, name='index'),
    path('bewerbungen/', views.board, name='board'),
    path('kommissionen/', views.juries, name='juries'),
    path('konto/', views.signup, name='signup'),
    path('bewerben/', views.apply, name='apply'),
    path('meine/', views.mine, name='mine'),
    path('meine/<int:pk>/annahme/', views.accept, name='accept'),
    path('bewerbung/<int:pk>/', views.application, name='application'),
    path('bewerbung/<int:pk>/entscheidung/', views.decide, name='decide'),
    path('bewerbungen/entscheiden/', views.decide_many, name='decide_many'),
    path('bewerbung/<int:pk>/unterlagen/', views.upload, name='upload'),
    path('unterlagen/<int:pk>/', views.document, name='document'),
    path('unterlagen/<int:pk>/entfernen/', views.discard, name='discard'),
    path('pruefung/<int:pk>/', views.audition, name='audition'),
    path('pruefung/<int:pk>/sichtung/', views.review, name='review'),
    path('pruefung/<int:pk>/termine/', views.plan, name='plan'),
    path('pruefung/<int:pk>/weiter/', views.advance, name='advance'),
    path('pruefung/<int:pk>/abschluss/', views.close, name='close'),
    path('pruefung/<int:pk>/nachruecken/', views.move_up, name='move_up'),
]
