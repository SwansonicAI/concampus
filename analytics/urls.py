from django.urls import path

from . import views

app_name = 'analytics'
urlpatterns = [
    path('', views.index, name='index'),
    path('abfrage/', views.query, name='query'),
    path('datensaetze/', views.records, name='records'),
    path('amtlich/', views.official, name='official'),
]
