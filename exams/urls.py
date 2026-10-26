from django.urls import path

from . import views

app_name = 'exams'
urlpatterns = [
    path('', views.index, name='index'),
    path('anerkennung/', views.recognition, name='recognition'),
    path('leistungen/<int:pk>/', views.transcript, name='transcript'),
    path('leistungen/<int:pk>/diploma-supplement/', views.supplement, name='supplement'),
    path('abschlussarbeit/<int:pk>/', views.thesis, name='thesis'),
    path('neu/', views.edit, name='new'),
    path('<int:pk>/', views.exam, name='exam'),
    path('<int:pk>/bearbeiten/', views.edit, name='edit'),
    path('<int:pk>/termine/', views.plan, name='plan'),
    path('<int:pk>/freigabe/', views.release, name='release'),
    path('<int:pk>/erinnern/', views.remind, name='remind'),
    path('<int:pk>/liste/', views.roster, name='roster'),
    path('<int:pk>/noten/', views.marks, name='marks'),
    path('<int:pk>/anmelden/', views.register, name='register'),
    path('teilnahme/<int:pk>/ruecktritt/', views.withdraw, name='withdraw'),
    path('teilnahme/<int:pk>/abgabe/', views.submit, name='submit'),
]
