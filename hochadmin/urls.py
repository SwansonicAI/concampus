from django.conf import settings
from django.conf.urls.static import static
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
    path('einstellungen/', include('setup.urls')),
    path('', include('core.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
