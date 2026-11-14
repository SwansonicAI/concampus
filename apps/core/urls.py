from django.urls import path

from . import sso, views

urlpatterns = [
    path('', views.home, name='home'),
    path('demo/', views.demo, name='demo'),
    path('demo/moodle/', views.demo_moodle, name='demo_moodle'),
    path('demo/datum/', views.demo_day, name='demo_day'),
    path('zugang/', views.gate, name='gate'),
    path('barrierefreiheit/', views.accessibility, name='accessibility'),
    path('logo/', views.logo, name='logo'),
    path('suche/', views.search, name='search'),
    path('datum/', views.when, name='when'),
    path('termine/', views.day, name='day'),
    path('orte/', views.towns, name='towns'),
    path('konto/', views.account, name='account'),
    path('konto/sso/', sso.begin, name='sso'),
    path('konto/sso/acs/', sso.acs, name='sso_acs'),
    path('konto/sso/metadata/', sso.metadata, name='sso_metadata'),
    path('konto/bescheide/', views.delivery, name='delivery'),
    path('konto/daten/', views.copy, name='copy'),
    path('konto/adresse/<str:token>/', views.confirm_email, name='confirm_email'),
    path('konto/einwilligung/<int:pk>/widerruf/', views.revoke, name='revoke'),
    path('konto/beantragen/', views.ask, name='ask'),
    path('personen/', views.directory, name='directory'),
    path('personen/<int:pk>/', views.colleague, name='colleague'),
    path('aemter/<int:pk>/', views.office, name='office'),
    path('postfach/', views.inbox, name='inbox'),
    path('postfach/<int:pk>/', views.message, name='message'),
    path('postfach/<int:pk>/brief/', views.letter, name='letter'),
    path('verlauf/<str:table>/<int:row>/', views.history, name='history'),
    path('verlauf/<int:pk>/zuruecksetzen/', views.undo, name='undo'),
]
