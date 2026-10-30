from django.urls import path

from . import views

app_name = 'setup'
urlpatterns = [
    path('', views.index, name='index'),
    path('texte/', views.texts, name='texts'),
    path('texte/<str:key>/', views.text, name='text'),
    path('konten/<int:pk>/einladung/', views.invite, name='invite'),
    path('freigaben/', views.approvals, name='approvals'),
    path('exporte/', views.exports, name='exports'),
    path('verzeichnisse/', views.official, name='keys'),
    path('<str:key>/', views.listing, name='list'),
    path('<str:key>/neu/', views.edit, name='new'),
    path('<str:key>/<int:pk>/', views.edit, name='edit'),
    path('<str:key>/<int:pk>/loeschen/', views.delete, name='delete'),
]
