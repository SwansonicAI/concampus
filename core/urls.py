from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('demo/', views.demo, name='demo'),
    path('zugang/', views.gate, name='gate'),
    path('suche/', views.search, name='search'),
    path('datum/', views.when, name='when'),
    path('orte/', views.towns, name='towns'),
    path('konto/', views.account, name='account'),
    path('konto/bescheide/', views.delivery, name='delivery'),
    path('konto/einwilligung/<int:pk>/widerruf/', views.revoke, name='revoke'),
    path('postfach/', views.inbox, name='inbox'),
    path('postfach/<int:pk>/', views.message, name='message'),
    path('postfach/<int:pk>/brief/', views.letter, name='letter'),
    path('verlauf/<str:table>/<int:row>/', views.history, name='history'),
    path('verlauf/<int:pk>/zuruecksetzen/', views.undo, name='undo'),
]
