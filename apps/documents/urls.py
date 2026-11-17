from django.urls import path

from . import views
from .catalogue import KINDS

app_name = 'documents'
urlpatterns = [
    path('', views.index, name='index'),
    path('dateien/', views.artworks, name='artworks'),
    path('dateien/<int:pk>/', views.artwork, name='artwork'),
    path('dateien/<int:pk>/loeschen/', views.artwork_delete, name='artwork_delete'),
    *(path(f'{kind}/', views.listing, {'kind': kind}, name=kind) for kind in KINDS),  # a name each: the side menu marks pages by theirs
    path('<slug:kind>/neu/', views.copy, name='copy'),
    path('<slug:kind>/<slug:key>/', views.document, name='document'),
    path('<slug:kind>/<slug:key>/muster/', views.preview, name='preview'),
]
