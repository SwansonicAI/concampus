"""Sign-in through the Hochschule's identity provider with SAML 2.0, as Shibboleth and the DFN-AAI use it; ConCampus is the
service provider (settings.SAML). Whoever the provider vouches for signs in as the person ConCampus knows by that id; at
their first sign-in, as the one active person with the e-mail address it gives, who keeps the id from then on. Nobody is
made here: ConCampus knows its people from their application, enrolment or appointment. Only answers to ConCampus's own
requests count."""
from functools import cache
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser as Metadata
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from .backends import address
from .models import Person, SignIn

COOKIE = 'saml'  # the request's id until the answer comes back, on a POST from another site


@cache
def provider():
    """The identity provider's entity, addresses and certificate, from its metadata, read once a process."""
    source = settings.SAML['idp']
    return Metadata.parse_remote(source) if source.startswith('https://') else Metadata.parse(Path(source).read_text(encoding='utf-8'))


def config(request):
    """python3-saml's settings: the provider's, and ConCampus's own addresses on this host; 404 without a provider."""
    own = settings.SAML
    if not own['idp']:
        raise Http404
    base = request.build_absolute_uri('/')[:-1]
    sp = {
        'entityId': base + reverse('sso_metadata'),
        'assertionConsumerService': {'url': base + reverse('sso_acs'), 'binding': OneLogin_Saml2_Constants.BINDING_HTTP_POST},
    }
    if own['key']:  # to sign requests, and to have assertions encrypted
        sp |= {'privateKey': Path(own['key']).read_text(), 'x509cert': Path(own['cert']).read_text()}
    security = {'authnRequestsSigned': bool(own['key']), 'wantNameId': False, 'rejectDeprecatedAlgorithm': True}
    return Metadata.merge_settings({'strict': True, 'sp': sp, 'security': security}, provider())


def prepared(request):
    """The request as python3-saml reads it."""
    return {
        'https': 'on' if request.is_secure() else 'off', 'http_host': request.get_host(), 'script_name': request.path,
        'get_data': request.GET.copy(), 'post_data': request.POST.copy(),
    }


def onward(request, target):
    return target if url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()) else ''


@login_not_required
def begin(request):
    """Off to the identity provider, which sends the person back to `acs` and from there to where they were going."""
    auth = OneLogin_Saml2_Auth(prepared(request), config(request))
    response = redirect(auth.login(return_to=onward(request, request.GET.get('next', ''))))
    response.set_signed_cookie(
        COOKIE, auth.get_last_request_id(), max_age=600, secure=request.is_secure(), httponly=True,
        samesite='None' if request.is_secure() else 'Lax',
    )
    return response


def vouched(attributes):
    """Whom the identity provider vouches for: by their id; at the first sign-in by their e-mail address."""
    sso_id, mail = ((attributes.get(settings.SAML[name]) or [''])[0] for name in ('id', 'mail'))
    if not sso_id:
        return None, ''
    if found := Person.objects.filter(sso_id=sso_id, is_active=True).first():
        return found, sso_id
    first = list(Person.objects.filter(email__iexact=mail, is_active=True, sso_id=None)[:2]) if mail else []
    if len(first) != 1:
        return None, sso_id
    first[0].sso_id = sso_id
    first[0].save(update_fields=['sso_id'])
    return first[0], sso_id


@csrf_exempt  # the identity provider posts here; its signature and ConCampus's own request id stand in for the token
@login_not_required
@require_POST
def acs(request):
    auth = OneLogin_Saml2_Auth(prepared(request), config(request))
    asked = request.get_signed_cookie(COOKIE, None, max_age=600)
    if asked:
        auth.process_response(request_id=asked)
    person, sso_id = vouched(auth.get_attributes()) if asked and auth.is_authenticated() and not auth.get_errors() else (None, '')
    if person is None:
        SignIn.objects.create(username=sso_id[:150], ip=address(request), success=False)
        why = 'zu diesem Hochschulkonto gibt es in ConCampus kein Konto' if sso_id else 'die Antwort der Hochschule ließ sich nicht prüfen'
        messages.error(request, f'Anmeldung mit dem Hochschulkonto nicht möglich: {why}.')
        response = redirect('login')
    else:
        login(request, person, backend=settings.AUTHENTICATION_BACKENDS[0])
        response = redirect(onward(request, request.POST.get('RelayState', '')) or settings.LOGIN_REDIRECT_URL)
    response.delete_cookie(COOKIE, samesite='None' if request.is_secure() else 'Lax')
    return response


@login_not_required
def metadata(request):
    """ConCampus as a service provider, for the Hochschule's identity provider and the federation."""
    return HttpResponse(OneLogin_Saml2_Settings(config(request), sp_validation_only=True).get_sp_metadata(), content_type='application/samlmetadata+xml')
