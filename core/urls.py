from django.urls import path

from . import sso, views

urlpatterns = [
    path('', views.home, name='home'),
    path('demo/', views.demo, name='demo'),
    path('zugang/', views.gate, name='gate'),
    path('barrierefreiheit/', views.accessibility, name='accessibility'),
    path('logo/', views.logo, name='logo'),
    path('suche/', views.search, name='search'),
    path('datum/', views.when, name='when'),
    path('orte/', views.towns, name='towns'),
    path('konto/', views.account, name='account'),
    path('konto/sso/', sso.begin, name='sso'),
    path('konto/sso/acs/', sso.acs, name='sso_acs'),
    path('konto/sso/metadata/', sso.metadata, name='sso_metadata'),
    path('konto/bescheide/', views.delivery, name='delivery'),
    path('konto/daten/', views.copy, name='copy'),
    path('konto/adresse/<str:token>/', views.confirm_email, name='confirm_email'),
    path('konto/einwilligung/<int:pk>/widerruf/', views.revoke, name='revoke'),
    path('postfach/', views.inbox, name='inbox'),
    path('postfach/<int:pk>/', views.message, name='message'),
    path('postfach/<int:pk>/brief/', views.letter, name='letter'),
    path('verlauf/<str:table>/<int:row>/', views.history, name='history'),
    path('verlauf/<int:pk>/zuruecksetzen/', views.undo, name='undo'),
]
