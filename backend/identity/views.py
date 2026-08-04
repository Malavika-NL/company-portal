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
    return str(user_id)


def ensure_portal_identity(marketing_user_id, company, email=''):
    """Create the portal-side, passwordless link for a verified Marketing user."""
    profile = PortalProfile.objects.select_related('user').filter(marketing_user_id=marketing_user_id).first()
    if not profile:
        User = get_user_model()
        username = f'marketing-{marketing_user_id}'
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={'is_active': True, 'email': email},
        )
        if user.has_usable_password():
            user.set_unusable_password()
            user.save(update_fields=['password'])
        profile = PortalProfile.objects.create(user=user, marketing_user_id=marketing_user_id)
    normalized_email = str(email or '').strip().lower()
    if normalized_email and profile.user.email != normalized_email:
        profile.user.email = normalized_email
        profile.user.save(update_fields=['email'])
    if not profile.is_active or not profile.user.is_active:
        return None, None
    # VBSAI is the trusted company identity provider for this portal. An active
    # VBSAI user may select a portal company on first sign-in; the membership
    # is created then, so no separate portal/CRM account setup is required.
    membership = Membership.objects.filter(
        user=profile.user,
        company=company,
        is_active=True,
    ).first()
    if not membership and normalized_email.endswith('@vbsai.com'):
        membership, _ = Membership.objects.update_or_create(
            user=profile.user,
            company=company,
            defaults={'role': 'member', 'is_active': True},
        )
    return profile, membership


def find_bdcrm_account(email, company_id, portal_username='', role='member'):
    body = json.dumps({
        'email': email, 'company_id': company_id, 'provision': True,
        'portal_username': portal_username, 'role': role,
    }).encode()
    request = Request(settings.BDCRM_ACCOUNT_LOOKUP_URL, data=body, headers={
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
        'X-Company-ID': str(company_id),
        'Host': '192.168.1.56',
    }, method='POST')
    try:
        with urlopen(request, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    external_user_id = payload.get('external_user_id') if isinstance(payload, dict) else None
    return str(external_user_id) if external_user_id is not None and not isinstance(external_user_id, (dict, list, bool)) else None


def find_salespie_account(email, company_id, portal_username='', role='member'):
    request = Request(settings.SALESPIE_ACCOUNT_LOOKUP_URL, data=json.dumps({
        'email': email, 'company_id': company_id, 'provision': True,
        'portal_username': portal_username, 'role': role,
    }).encode(), headers={
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
        'X-Company-ID': str(company_id),
        'Host': '192.168.1.56',
    }, method='POST')
    try:
        with urlopen(request, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    user_id = payload.get('external_user_id') if isinstance(payload, dict) else None
    return str(user_id) if user_id is not None and not isinstance(user_id, (dict, list, bool)) else None


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

        marketing_user_id = marketing_credentials_login(email, password, company.id)
        if marketing_user_id is MARKETING_LOGIN_UNAVAILABLE:
            return Response({'detail': 'Marketing CRM backend is not running. Start Marketing CRM and try again.'}, status=502)
        if marketing_user_id is None:
            return Response({'detail': 'Invalid Marketing CRM email or password.'}, status=401)
        profile, membership = ensure_portal_identity(marketing_user_id, company, email=email)
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
        apps = [
            {'key': 'marketing_crm', 'name': 'Marketing CRM', 'launch_url': settings.MARKETING_CRM_URL},
            {'key': 'salespie', 'name': 'SalesPie', 'launch_url': settings.SALESPIE_CRM_URL},
            {'key': 'bdcrm', 'name': 'BDCRM', 'launch_url': settings.BDCRM_URL},
        ]
        return Response({'company': {'id': membership.company_id, 'code': membership.company.code, 'name': membership.company.name}, 'applications': apps})


class SSOPreflightView(APIView):
    """Check downstream SSO credentials without consuming or creating a login."""

    def get(self, request):
        company_id = request.auth.get('company_id') if request.auth else None
        membership = Membership.objects.filter(
            user=request.user, company_id=company_id, is_active=True,
        ).first()
        if not membership:
            return Response({'detail': 'Select an authorized company.'}, status=403)

        result = {}
        for application, target_url in (
            ('salespie', settings.SALESPIE_ACCOUNT_LOOKUP_URL),
            ('bdcrm', settings.BDCRM_ACCOUNT_LOOKUP_URL),
        ):
            probe = Request(
                target_url,
                data=json.dumps({'email': request.user.email, 'company_id': company_id, 'provision': False}).encode(),
                headers={
                    'Accept': 'application/json', 'Content-Type': 'application/json',
                    'X-Portal-SSO-Secret': settings.PORTAL_SSO_SHARED_SECRET,
                    'X-Company-ID': str(company_id),
                    'Host': '192.168.1.56',
                }, method='POST',
            )
            try:
                with urlopen(probe, timeout=settings.MARKETING_CRM_TOKEN_TIMEOUT_SECONDS):
                    result[application] = {'ready': True}
            except HTTPError as exc:
                # 404 only means this user has not been provisioned yet; the
                # protected Portal-to-CRM credential was accepted.
                result[application] = {'ready': exc.code == 404, 'status': exc.code}
            except (URLError, TimeoutError, ValueError):
                result[application] = {'ready': False, 'status': 'unreachable'}
        healthy = all(item['ready'] for item in result.values())
        return Response({'ready': healthy, 'applications': result}, status=200 if healthy else 503)


class SSOLaunchView(APIView):
    def post(self, request):
        application = str(request.data.get('application') or '').strip()
        if application not in dict(SSOCode.APPLICATIONS):
            return Response({'detail': 'Unknown application.'}, status=400)
        company_id = request.auth.get('company_id') if request.auth else None
        membership = Membership.objects.select_related('company').filter(user=request.user, company_id=company_id, is_active=True).first()
        if not membership:
            return Response({'detail': 'Select an authorized company.'}, status=403)
        url = {'marketing_crm': settings.MARKETING_CRM_URL, 'salespie': settings.SALESPIE_CRM_URL, 'bdcrm': settings.BDCRM_URL}[application]
        services_ready, failed_services = ensure_application_services(application)
        if not services_ready:
            return Response({
                'detail': f'{failed_services} could not be started. Check the project folder and try again.',
            }, status=502)
        if application == 'marketing_crm':
            linked = PortalProfile.objects.filter(user=request.user, is_active=True).exists()
        else:
            mapping = ApplicationUserMapping.objects.filter(
                user=request.user,
                company=membership.company,
                application=application,
                is_active=True,
            ).first()
            external_user_id = mapping.external_user_id if mapping else None

            # Each downstream CRM is keyed by the verified Marketing email.
            # Resolve it on every launch: this repairs renamed/deactivated local
            # accounts and provisions a passwordless account on first use.
            email = str(request.user.email or '').strip().lower()
            resolved_user_id = (
                find_salespie_account(email, membership.company_id, request.user.username, membership.role)
                if application == 'salespie'
                else find_bdcrm_account(email, membership.company_id, request.user.username, membership.role)
            ) if email else None
            external_user_id = resolved_user_id or external_user_id
            if external_user_id:
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
                'detail': f'No active {application} account uses your Marketing CRM email address.',
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
                profile = PortalProfile.objects.filter(user_id=handoff.user_id, is_active=True).first()
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
