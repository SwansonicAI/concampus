from django.urls import path

from . import views
from .catalogue import KINDS

app_name = 'documents'
urlpatterns = [
    path('', views.index, name='index'),
    path('dateien/', views.artworks, name='artworks'),
    path('dateien/<int:pk>/', views.artwork, name='artwork'),
    path('dateien/<int:pk>/loeschen/', views.artwork_delete, name='artwork_delete'),
    path('vorlagen/', views.letterheads_list, name='letterheads'),  # before the documents' own, whose kind it would take for one
    path('vorlagen/neu/', views.letterhead_copy, name='letterhead_copy'),
    path('vorlagen/<slug:key>/', views.letterhead, name='letterhead'),
    path('vorlagen/<slug:key>/muster/', views.letterhead_preview, name='letterhead_preview'),
    *(path(f'{kind}/', views.listing, {'kind': kind}, name=kind) for kind in KINDS),  # a name each: the side menu marks pages by theirs
    path('<slug:kind>/neu/', views.copy, name='copy'),
    path('<slug:kind>/<slug:key>/', views.document, name='document'),
    path('<slug:kind>/<slug:key>/muster/', views.preview, name='preview'),
]
