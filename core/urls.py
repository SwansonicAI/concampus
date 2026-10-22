from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('suche/', views.search, name='search'),
    path('datum/', views.when, name='when'),
    path('postfach/', views.inbox, name='inbox'),
    path('postfach/<int:pk>/', views.message, name='message'),
    path('postfach/<int:pk>/brief/', views.letter, name='letter'),
    path('verlauf/<str:table>/<int:row>/', views.history, name='history'),
    path('verlauf/<int:pk>/zuruecksetzen/', views.undo, name='undo'),
]
