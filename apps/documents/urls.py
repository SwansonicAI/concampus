from django.urls import path

from . import views
from .catalogue import KINDS

app_name = 'documents'
urlpatterns = [
    path('', views.index, name='index'),
    path('vorlagen/dateien/', views.artworks, name='artworks'),
    path('vorlagen/dateien/<int:pk>/', views.artwork, name='artwork'),
    path('vorlagen/dateien/<int:pk>/loeschen/', views.artwork_delete, name='artwork_delete'),
    path('vorlagen/<slug:key>/', views.editor, name='paper'),
    path('vorlagen/<slug:key>/muster/', views.preview, name='paper_preview'),
    path('vorlagen/<slug:key>/inhalt/', views.paper_content, name='paper_content'),
    *(path(f'{kind}/', views.listing, {'kind': kind}, name=kind) for kind in KINDS),  # a name each: the side menu marks pages by theirs
    path('<slug:kind>/<str:key>/', views.document, name='document'),
    path('<slug:kind>/<str:key>/vorschau/', views.document_preview, name='document_preview'),
]
