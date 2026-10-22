from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('konto/', include('django.contrib.auth.urls')),
    path('studierende/', include('students.urls')),
    path('studiengaenge/', include('curriculum.urls')),
    path('lehre/', include('courses.urls')),
    path('pruefungen/', include('exams.urls')),
    path('aufnahme/', include('admissions.urls')),
    path('raeume/', include('rooms.urls')),
    path('auswertungen/', include('analytics.urls')),
    path('einstellungen/', include('setup.urls')),
    path('', include('core.urls')),
]  # uploads are not served as files: admissions:document checks who may see each
