"""
WSGI config for concampus project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'concampus.settings')

# A server without its database would keep its data in a file lost with the container: it does not start. Without mail
# it starts, saying so in its log; the Postfach has everything. Development (DEBUG=1) uses SQLite and prints mail.
if os.environ.get('DEBUG') != '1' and not os.environ.get('DATABASE_URL'):
    raise RuntimeError('DATABASE_URL fehlt in der Umgebung (.env).')

application = get_wsgi_application()

if os.environ.get('DEBUG') != '1' and not os.environ.get('EMAIL_URL'):
    logging.getLogger('concampus').warning('EMAIL_URL fehlt: keine E-Mails, nur das Postfach.')
