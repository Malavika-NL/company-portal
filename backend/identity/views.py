import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .local_services import ensure_application_services
from .models import (
    ApplicationUserMapping,
    Company,
    MarketingAuthorizationState,
    Membership,
    PortalProfile,
    SSOCode,
    ensure_default_companies,
)


MARKETING_LOGIN_UNAVAILABLE = object()

# Distinguishes "caller has no app_access info" (e.g. the OAuth callback,
# which does not capture it) from an explicit None/list value.
APP_ACCESS_UNSPECIFIED = object()


def token_payload(user, membership):
    refresh = RefreshToken.for_user(user)
    refresh['company_id'] = membership.company_id
    refresh['company_code'] = membership.company.code
    refresh['role'] = membership.role
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'company': {
            'id': membership.company_id,
            'code': membership.company.code,
            'name': membership.company.name,
            'role': membership.role,
        },
    }


def marketing_token_exchange(code, company_id):
    """Exchange the browser code with Marketing CRM over a back-channel."""
    body = urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.MARKETING_CRM_REDIRECT_URI,
        'client_id': settings.MARKETING_CRM_CLIENT_ID,
        'client_secret': settings.MARKETING_CRM_CLIENT_SECRET,
    }).encode()
    request = Request(
        settings.MARKETING_CRM_TOKEN_URL,
        data=body,
        headers={'Accept': 'application/json', 'X-Company-ID': str(company_id)},
        method='POST',
    )
    try:
        with urlopen(request, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None

    # The provider contract deliberately returns only its stable local primary key.
    marketing_user_id = payload.get('marketing_user_id')
    if marketing_user_id is None or isinstance(marketing_user_id, (dict, list, bool)):
        return None
    return str(marketing_user_id)


def marketing_credentials_login(email, password, company_id):
    """Verify credentials with Marketing CRM without storing them in the portal."""
    body = json.dumps({'email': email, 'password': password}).encode()
    request = Request(
        settings.MARKETING_CRM_LOGIN_URL,
        data=body,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            # The company header is an internal portal-to-Marketing contract.
            # Marketing must not honour it for a normal browser request.
            'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
            'X-Company-ID': str(company_id),
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            return None
        return MARKETING_LOGIN_UNAVAILABLE
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return MARKETING_LOGIN_UNAVAILABLE
    if not isinstance(payload, dict):
        return MARKETING_LOGIN_UNAVAILABLE
    user = payload.get('user')
    user_id = user.get('id') if isinstance(user, dict) else payload.get('user_id')
    if user_id is None or isinstance(user_id, (dict, list, bool)):
        return MARKETING_LOGIN_UNAVAILABLE
    # Do not create a portal membership from a response for another company.
    # This value is supplied by Marketing only after it has authenticated the
    # portal service and checked the user's own tenant assignment.
    response_company_id = (
        user.get('company_id') if isinstance(user, dict) else None
    ) or payload.get('company_id')
    if response_company_id is not None:
        try:
            if int(response_company_id) != int(company_id):
                return None
        except (TypeError, ValueError):
            return None
    raw_app_access = user.get('app_access') if isinstance(user, dict) else None
    if isinstance(raw_app_access, list):
        app_access = sorted({str(item) for item in raw_app_access if isinstance(item, str)})
    else:
        # Key missing (or not a list) -- an older Marketing build that
        # predates the app_access contract. ensure_portal_identity() treats
        # None as "unknown", and WorkspaceView falls back to every tile.
        app_access = None
    return {
        'id': str(user_id),
        'email': str(user.get('email') or '').strip().lower() if isinstance(user, dict) else '',
        'first_name': str(user.get('firstName') or '').strip() if isinstance(user, dict) else '',
        'last_name': str(user.get('lastName') or '').strip() if isinstance(user, dict) else '',
        'app_access': app_access,
    }


def ensure_portal_identity(
    marketing_user_id, company, email='', first_name='', last_name='',
    app_access=APP_ACCESS_UNSPECIFIED,
):
    """Create a passwordless identity inside the selected company only.

    A successful CRM authentication is sufficient to use the selected
    company.  Membership is retained as the portal's internal company/role
    record, but it is created automatically; administrators no longer need
    to pre-provision it before a user can sign in.
    """
    profile = PortalProfile.objects.select_related('user').filter(
        company=company, marketing_user_id=marketing_user_id,
    ).first()
    if not profile:
        User = get_user_model()
        username = f'marketing-{company.pk}-{marketing_user_id}'
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={'is_active': True, 'email': email, 'first_name': first_name, 'last_name': last_name},
        )
        if user.has_usable_password():
            user.set_unusable_password()
            user.save(update_fields=['password'])
        profile = PortalProfile.objects.create(
            user=user, company=company, marketing_user_id=marketing_user_id,
        )
    # These are user fields only, held on the company-scoped local identity so
    # downstream CRM account resolution does not need a cross-company lookup.
    normalized_email = str(email or '').strip().lower()
    if normalized_email and profile.user.email != normalized_email:
        profile.user.email = normalized_email
    if first_name and profile.user.first_name != first_name:
        profile.user.first_name = first_name
    if last_name and profile.user.last_name != last_name:
        profile.user.last_name = last_name
    if normalized_email or first_name or last_name:
        profile.user.save(update_fields=['email', 'first_name', 'last_name'])
    if app_access is not APP_ACCESS_UNSPECIFIED and profile.app_access != app_access:
        profile.app_access = app_access
        profile.save(update_fields=['app_access'])
    if not profile.is_active or not profile.user.is_active:
        return None, None
    membership, created = Membership.objects.get_or_create(
        user=profile.user,
        company=company,
        defaults={'role': 'user', 'is_active': True},
    )
    # A user who can successfully authenticate with the CRM must not remain
    # blocked by a stale, manually-disabled portal membership.
    if not membership.is_active:
        membership.is_active = True
        membership.save(update_fields=['is_active'])
    return profile, membership


def find_bdcrm_account(email, company_id, portal_username='', role='user'):
    """Resolve-only lookup. Marketing CRM is the sole provisioning origin
    (see provision_workspace_accounts() in email_campaign/serializers.py) --
    this must never create or reactivate a downstream account.

    Returns (external_user_id, is_active) or (None, None) if no account was
    found or the lookup failed.
    """
    body = json.dumps({
        'email': email, 'company_id': company_id, 'provision': False,
        'portal_username': portal_username, 'role': role,
    }).encode()
    request = Request(settings.BDCRM_ACCOUNT_LOOKUP_URL, data=body, headers={
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
        'X-Company-ID': str(company_id),
        'Host': '192.168.1.94',
    }, method='POST')
    try:
        with urlopen(request, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None, None
    external_user_id = payload.get('external_user_id') if isinstance(payload, dict) else None
    if external_user_id is None or isinstance(external_user_id, (dict, list, bool)):
        return None, None
    # A resolve-only 200 always carries is_active now; an absent key means an
    # older receiver build that only ever matched active accounts.
    is_active = payload.get('is_active') if isinstance(payload, dict) else None
    return str(external_user_id), (True if is_active is None else bool(is_active))


def find_salespie_account(email, company_id, portal_username='', role='user', display_name=''):
    """Resolve-only lookup; see find_bdcrm_account() docstring."""
    request = Request(settings.SALESPIE_ACCOUNT_LOOKUP_URL, data=json.dumps({
        'email': email, 'company_id': company_id, 'provision': False,
        'portal_username': portal_username, 'role': role, 'display_name': display_name,
    }).encode(), headers={
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
        'X-Company-ID': str(company_id),
        'Host': '192.168.1.94',
    }, method='POST')
    try:
        with urlopen(request, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None, None
    user_id = payload.get('external_user_id') if isinstance(payload, dict) else None
    if user_id is None or isinstance(user_id, (dict, list, bool)):
        return None, None
    is_active = payload.get('is_active') if isinstance(payload, dict) else None
    return str(user_id), (True if is_active is None else bool(is_active))


class CompaniesView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        ensure_default_companies()
        return Response(list(Company.objects.filter(is_active=True).values('id', 'code', 'name')))


class MarketingLoginStartView(APIView):
    """Start login after a public NL/VBS company selection; no portal login exists."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        company_code = str(request.data.get('company_code') or '').strip().lower()
        company = Company.objects.filter(code=company_code, is_active=True).first()
        if not company:
            return Response({'detail': 'Choose an active company.'}, status=400)

        state = secrets.token_urlsafe(32)
        MarketingAuthorizationState.objects.create(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            company=company,
            expires_at=timezone.now() + timedelta(seconds=settings.MARKETING_CRM_AUTH_STATE_TTL_SECONDS),
        )
        query = urlencode({
            'response_type': 'code',
            'client_id': settings.MARKETING_CRM_CLIENT_ID,
            'redirect_uri': settings.MARKETING_CRM_REDIRECT_URI,
            'state': state,
        })
        separator = '&' if '?' in settings.MARKETING_CRM_AUTHORIZE_URL else '?'
        return Response({'authorization_url': f'{settings.MARKETING_CRM_AUTHORIZE_URL}{separator}{query}'})


class MarketingCredentialsLoginView(APIView):
    """Company Portal's common login screen verifies credentials with Marketing CRM."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        company_code = str(request.data.get('company_code') or '').strip().lower()
        email = str(request.data.get('email') or '').strip().lower()
        password = str(request.data.get('password') or '')
        company = Company.objects.filter(code=company_code, is_active=True).first()
        if not company:
            return Response({'detail': 'Choose an active company.'}, status=400)
        if not email or not password:
            return Response({'detail': 'Enter your Marketing CRM email and password.'}, status=400)

        services_ready, failed_services = ensure_application_services('marketing_crm', include_frontend=False)
        if not services_ready:
            return Response({
                'detail': f'{failed_services} could not be started. Check the Marketing CRM project and try again.',
            }, status=502)

        marketing_user = marketing_credentials_login(email, password, company.id)
        if marketing_user is MARKETING_LOGIN_UNAVAILABLE:
            return Response({'detail': 'Marketing CRM backend is not running. Start Marketing CRM and try again.'}, status=502)
        if marketing_user is None:
            return Response({'detail': 'Invalid Marketing CRM email or password.'}, status=401)
        profile, membership = ensure_portal_identity(
            marketing_user['id'], company,
            email=marketing_user['email'] or email,
            first_name=marketing_user['first_name'],
            last_name=marketing_user['last_name'],
            app_access=marketing_user['app_access'],
        )
        if not profile:
            return Response({'detail': 'This Marketing CRM user is inactive.'}, status=403)
        if not membership:
            return Response({'detail': 'This Marketing CRM user is not assigned to the selected company.'}, status=403)
        return Response(token_payload(profile.user, membership))


class MarketingLoginCallbackView(APIView):
    """Validate the redirect state, exchange the one-time code, then create a portal session."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        code = str(request.query_params.get('code') or '')
        state = str(request.query_params.get('state') or '')
        if not code or not state:
            return Response({'detail': 'Missing Marketing authorization response.'}, status=400)

        with transaction.atomic():
            handoff = MarketingAuthorizationState.objects.select_for_update().select_related('company').filter(
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).first()
            if not handoff:
                return Response({'detail': 'Authorization state expired or already used.'}, status=401)
            # Consume before calling the provider, so a concurrent callback cannot redeem it.
            handoff.consumed_at = timezone.now()
            handoff.save(update_fields=['consumed_at'])
            company_id = handoff.company_id

        marketing_user_id = marketing_token_exchange(code, company_id)
        if marketing_user_id is None:
            return Response({'detail': 'Marketing CRM authorization code could not be exchanged.'}, status=401)

        company = Company.objects.filter(pk=company_id, is_active=True).first()
        if not company:
            return Response({'detail': 'Choose an active company.'}, status=400)
        profile, membership = ensure_portal_identity(marketing_user_id, company)
        if not profile:
            return Response({'detail': 'This Marketing CRM user is inactive.'}, status=403)
        if not membership:
            return Response({'detail': 'This Marketing CRM user is not assigned to the selected company.'}, status=403)
        payload = token_payload(profile.user, membership)
        # Tokens are placed in the URL fragment: browsers do not send fragments
        # to servers, and the React app immediately replaces the URL.
        fragment = urlencode({
            'access': payload['access'], 'refresh': payload['refresh'],
            'company_id': membership.company_id, 'company_code': membership.company.code,
            'company_name': membership.company.name, 'role': membership.role,
        })
        return HttpResponseRedirect(f'{settings.PORTAL_FRONTEND_URL}/#auth={fragment}')


class WorkspaceView(APIView):
    def get(self, request):
        company_id = request.auth.get('company_id') if request.auth else None
        membership = Membership.objects.select_related('company').filter(user=request.user, company_id=company_id, is_active=True).first()
        if not membership:
            return Response({'detail': 'Select an authorized company.'}, status=403)
        profile = PortalProfile.objects.filter(user=request.user, company_id=company_id, is_active=True).first()
        app_access = profile.app_access if profile else None
        
        apps = [
            {'key': 'marketing_crm', 'name': 'Marketing CRM', 'launch_url': settings.MARKETING_CRM_URL, 'entitled': True},
            {
                'key': 'salespie', 'name': 'SalesPie', 'launch_url': settings.SALESPIE_CRM_URL,
                'entitled': app_access is None or 'salespie' in app_access,
            },
            {
                'key': 'bdcrm', 'name': 'BDCRM', 'launch_url': settings.BDCRM_URL,
                'entitled': app_access is None or 'bdcrm' in app_access,
            },
        ]
        return Response({'company': {'id': membership.company_id, 'code': membership.company.code, 'name': membership.company.name}, 'applications': apps})


class SSOPreflightView(APIView):
    """Report whether the signed-in user is entitled to each downstream CRM.

    """

    def get(self, request):
        company_id = request.auth.get('company_id') if request.auth else None
        membership = Membership.objects.filter(
            user=request.user, company_id=company_id, is_active=True,
        ).first()
        if not membership:
            return Response({'detail': 'Select an authorized company.'}, status=403)

        profile = PortalProfile.objects.filter(user=request.user, company_id=company_id, is_active=True).first()
        app_access = profile.app_access if profile else None

        result = {}
        for application, target_url in (
            ('salespie', settings.SALESPIE_ACCOUNT_LOOKUP_URL),
            ('bdcrm', settings.BDCRM_ACCOUNT_LOOKUP_URL),
        ):
            if app_access is not None and application not in app_access:
                result[application] = {'status': 'not_entitled'}
                continue

            probe = Request(
                target_url,
                data=json.dumps({'email': request.user.email, 'company_id': company_id, 'provision': False}).encode(),
                headers={
                    'Accept': 'application/json', 'Content-Type': 'application/json',
                    'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
                    'X-Company-ID': str(company_id),
                    'Host': '192.168.1.94',
                }, method='POST',
            )
            try:
                with urlopen(probe, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
                    payload = json.loads(response.read().decode())
                # A resolve-only 200 always carries is_active now; an absent
                # key means an older receiver build that only matched active
                # accounts.
                is_active = payload.get('is_active') if isinstance(payload, dict) else None
                if is_active is not False:
                    result[application] = {'status': 'entitled'}
                elif app_access is not None:
                    # Marketing granted this app but the downstream account is
                    # switched off -- a provisioning gap, not a missing grant.
                    result[application] = {'status': 'not_provisioned'}
                else:
                    result[application] = {'status': 'not_entitled'}
            except HTTPError as exc:
                if exc.code != 404:
                    result[application] = {'status': 'unreachable'}
                elif app_access is not None:
                    # Entitled in Marketing, but no downstream row exists yet.
                    result[application] = {'status': 'not_provisioned'}
                else:
                    result[application] = {'status': 'not_entitled'}
            except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
                result[application] = {'status': 'unreachable'}

        # A user entitled to one CRM but not another is a normal state, not a
        # failure -- 503 is reserved for every probe having failed outright.
        all_unreachable = all(item['status'] == 'unreachable' for item in result.values())
        return Response({'ready': not all_unreachable, 'applications': result}, status=503 if all_unreachable else 200)


class SSOLaunchView(APIView):
    def post(self, request):
        application = str(request.data.get('application') or '').strip()
        if application not in dict(SSOCode.APPLICATIONS):
            return Response({'detail': 'Unknown application.'}, status=400)
        company_id = request.auth.get('company_id') if request.auth else None
        membership = Membership.objects.select_related('company').filter(user=request.user, company_id=company_id, is_active=True).first()
        if not membership:
            return Response({'detail': 'Select an authorized company.'}, status=403)
        if application == 'marketing_crm':
            # Keep the user on the same LAN host used to access the Portal.
            # Only the service port changes: Portal 8002 -> Marketing CRM 8000.
            host = request.get_host().rsplit(':', 1)[0]
            url = f'{request.scheme}://{host}:8000'
        else:
            url = {'salespie': settings.SALESPIE_CRM_URL, 'bdcrm': settings.BDCRM_URL}[application]
        services_ready, failed_services = ensure_application_services(application)
        if not services_ready:
            return Response({
                'detail': f'{failed_services} could not be started. Check the project folder and try again.',
            }, status=502)
        if application == 'marketing_crm':
            linked = PortalProfile.objects.filter(
                user=request.user, company=membership.company, is_active=True,
            ).exists()
        else:
            profile = PortalProfile.objects.filter(
                user=request.user, company=membership.company, is_active=True,
            ).first()
            app_access = profile.app_access if profile else None
            if app_access is not None and application not in app_access:
                
                linked = False
            else:
                mapping = ApplicationUserMapping.objects.filter(
                    user=request.user,
                    company=membership.company,
                    application=application,
                    is_active=True,
                ).first()
                external_user_id = mapping.external_user_id if mapping else None

                
                email = str(request.user.email or '').strip().lower()
                resolved_user_id, resolved_is_active = (
                    find_salespie_account(
                        email, membership.company_id, request.user.username, membership.role,
                        request.user.get_full_name(),
                    )
                    if application == 'salespie'
                    else find_bdcrm_account(email, membership.company_id, request.user.username, membership.role)
                ) if email else (None, None)

                if resolved_user_id is not None and not resolved_is_active:
                    
                    if mapping:
                        ApplicationUserMapping.objects.filter(pk=mapping.pk).update(is_active=False)
                    external_user_id = None
                elif resolved_user_id is not None:
                    external_user_id = resolved_user_id
                    ApplicationUserMapping.objects.update_or_create(
                        user=request.user,
                        company=membership.company,
                        application=application,
                        defaults={'external_user_id': external_user_id, 'is_active': True},
                    )
                linked = bool(external_user_id)
        if not linked:
            return Response({
                'code': 'ACCOUNT_NOT_LINKED',
                'detail': 'No access. Contact your Marketing CRM administrator to enable this application.',
            }, status=403)
        raw = secrets.token_urlsafe(32)
        SSOCode.objects.create(
            code_hash=hashlib.sha256(raw.encode()).hexdigest(), user=request.user,
            company=membership.company, application=application,
            expires_at=timezone.now() + timedelta(seconds=settings.PORTAL_SSO_CODE_TTL_SECONDS),
        )
        return Response({'launch_url': f"{url}{'&' if '?' in url else '?'}sso_code={raw}"})


class SSOExchangeView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        if not hmac.compare_digest(request.headers.get('X-Portal-SSO-Secret', ''), settings.PORTAL_SSO_SHARED_SECRET):
            return Response({'detail': 'Invalid SSO client credentials.'}, status=403)
        app, raw = str(request.data.get('application') or ''), str(request.data.get('code') or '')
        with transaction.atomic():
            handoff = SSOCode.objects.select_for_update().select_related('company').filter(
                code_hash=hashlib.sha256(raw.encode()).hexdigest(), application=app,
                consumed_at__isnull=True, expires_at__gt=timezone.now(),
            ).first()
            if not handoff:
                return Response({'detail': 'Code expired or already used.'}, status=401)
            if app == 'marketing_crm':
                profile = PortalProfile.objects.filter(
                    user_id=handoff.user_id, company_id=handoff.company_id, is_active=True,
                ).first()
                external_user_id = profile.marketing_user_id if profile else None
            else:
                mapping = ApplicationUserMapping.objects.filter(
                    user_id=handoff.user_id, company_id=handoff.company_id, application=app, is_active=True,
                ).first()
                external_user_id = mapping.external_user_id if mapping else None
            if not external_user_id:
                return Response({'code': 'ACCOUNT_NOT_LINKED', 'detail': 'No local CRM account is linked.'}, status=403)
            handoff.consumed_at = timezone.now()
            handoff.save(update_fields=['consumed_at'])
        return Response({'external_user_id': external_user_id, 'company': {'id': handoff.company_id, 'code': handoff.company.code, 'name': handoff.company.name}})
