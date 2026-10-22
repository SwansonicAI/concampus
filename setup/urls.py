from django.urls import path

from . import views

app_name = 'setup'
urlpatterns = [
    path('', views.index, name='index'),
    path('texte/', views.texts, name='texts'),
    path('texte/<str:key>/', views.text, name='text'),
    path('<str:key>/', views.listing, name='list'),
    path('<str:key>/neu/', views.edit, name='new'),
    path('<str:key>/<int:pk>/', views.edit, name='edit'),
    path('<str:key>/<int:pk>/loeschen/', views.delete, name='delete'),
]
