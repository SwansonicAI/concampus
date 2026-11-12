from django.urls import path

from .views import exam, participation, recognition, thesis, transcripts

app_name = 'exams'
urlpatterns = [
    path('', exam.index, name='index'),
    path('anerkennung/', recognition.recognition, name='recognition'),
    path('leistungen/<int:pk>/', transcripts.transcript, name='transcript'),
    path('leistungen/<int:pk>/diploma-supplement/', transcripts.supplement, name='supplement'),
    path('leistungen/<int:pk>/ausstellen/', transcripts.issue, name='issue'),
    path('leistungen/<int:pk>/urkunde/', transcripts.certificate, name='certificate'),
    path('abschlussarbeit/<int:pk>/', thesis.thesis, name='thesis'),
    path('neu/', exam.edit, name='new'),
    path('<int:pk>/', exam.exam, name='exam'),
    path('<int:pk>/bearbeiten/', exam.edit, name='edit'),
    path('<int:pk>/termine/', exam.plan, name='plan'),
    path('<int:pk>/tag/', exam.book, name='book'),
    path('<int:pk>/freigabe/', exam.release, name='release'),
    path('<int:pk>/erinnern/', exam.remind, name='remind'),
    path('<int:pk>/liste/', exam.roster, name='roster'),
    path('<int:pk>/noten/', exam.marks, name='marks'),
    path('<int:pk>/protokoll/', exam.protocol, name='protocol'),
    path('<int:pk>/attest/', participation.attest, name='attest'),
    path('studium/<int:pk>/nachteilsausgleich/', participation.compensate, name='compensate'),
    path('<int:pk>/anmelden/', participation.register, name='register'),
    path('teilnahme/<int:pk>/ruecktritt/', participation.withdraw, name='withdraw'),
    path('teilnahme/<int:pk>/abgabe/', participation.submit, name='submit'),
]
