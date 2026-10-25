from django.contrib import admin
from django.contrib.auth.views import LoginView
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('konto/login/', LoginView.as_view(redirect_authenticated_user=True), name='login'),  # signed in already: on to the start page
    path('konto/', include('django.contrib.auth.urls')),
    path('studierende/', include('students.urls')),
    path('studiengaenge/', include('curriculum.urls')),
    path('lehre/', include('courses.urls')),
    path('pruefungen/', include('exams.urls')),
    path('aufnahme/', include('admissions.urls')),
    path('raeume/', include('rooms.urls')),
    path('instrumente/', include('instruments.urls')),
    path('auswertungen/', include('analytics.urls')),
    path('einstellungen/', include('setup.urls')),
    path('', include('core.urls')),
]  # uploads are not served as files: admissions:document checks who may see each
