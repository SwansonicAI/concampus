from django.urls import path

from . import views

app_name = 'mobility'
urlpatterns = [
    path('', views.index, name='index'),
    path('partner/', views.partners, name='partners'),
    path('mein-aufenthalt/', views.mine, name='mine'),
    path('<int:pk>/', views.mobility, name='mobility'),
]
