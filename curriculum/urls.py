from django.urls import path

from . import views

app_name = 'curriculum'
urlpatterns = [
    path('', views.index, name='index'),
    path('<int:pk>/', views.program, name='program'),
    path('<int:pk>/kopieren/', views.copy, name='copy'),
]
