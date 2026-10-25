from django.urls import path

from . import views

app_name = 'instruments'
urlpatterns = [
    path('', views.index, name='index'),
    path('meine/', views.mine, name='mine'),
    path('<int:pk>/', views.instrument, name='instrument'),
    path('<int:pk>/verleihen/', views.lend, name='lend'),
    path('leihe/<int:pk>/rueckgabe/', views.give_back, name='give_back'),
]
