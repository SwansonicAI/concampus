from django.urls import path

from . import papers, views

app_name = 'setup'
urlpatterns = [
    path('', views.index, name='index'),
    path('briefboegen/', papers.papers, name='papers'),
    path('briefboegen/dateien/', papers.artworks, name='artworks'),
    path('briefboegen/dateien/<int:pk>/', papers.artwork, name='artwork'),
    path('briefboegen/dateien/<int:pk>/loeschen/', papers.artwork_delete, name='artwork_delete'),
    path('briefboegen/<slug:key>/', papers.editor, name='paper'),
    path('briefboegen/<slug:key>/muster/', papers.preview, name='paper_preview'),
    path('texte/', views.texts, name='texts'),
    path('texte/<str:key>/', views.text, name='text'),
    path('konten/<int:pk>/einladung/', views.invite, name='invite'),
    path('lehrende/laden/', views.staff_import, name='staff_import'),
    path('lehrende/einladen/', views.invite_new, name='invite_new'),
    path('freigaben/', views.approvals, name='approvals'),
    path('rechte/', views.rights, name='rights'),
    path('exporte/', views.exports, name='exports'),
    path('anmeldungen/', views.sign_ins, name='sign_ins'),
    path('barrierefreiheit/', views.feedback, name='feedback'),
    path('verzeichnisse/', views.official, name='keys'),
    path('<str:key>/', views.listing, name='list'),
    path('<str:key>/neu/', views.edit, name='new'),
    path('<str:key>/<int:pk>/', views.edit, name='edit'),
    path('<str:key>/<int:pk>/loeschen/', views.delete, name='delete'),
    path('<str:key>/<int:pk>/verschieben/', views.move, name='move'),
]
