import json
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import ApplicationUserMapping, Company, MarketingAuthorizationState, Membership, PortalProfile
from .views import ensure_portal_identity, marketing_credentials_login


class MarketingProviderLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(code='nl-technologies-test', name='NL Technologies Test')
        self.user = User.objects.create_user(username='portal-identity', password='legacy-password')
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        self.profile = PortalProfile.objects.create(
            user=self.user, company=self.company, marketing_user_id='15',
        )
        Membership.objects.create(user=self.user, company=self.company)

    def test_companies_endpoint_initializes_default_companies(self):
        Company.objects.all().delete()

        response = self.client.get('/api/companies/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {company['code'] for company in response.data},
            {'nl-technologies', 'vbs'},
        )

    def test_start_redirects_to_marketing_with_state(self):
        response = self.client.post('/api/auth/marketing/start/', {'company_code': self.company.code}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('authorization_url', response.data)
        self.assertIn('state=', response.data['authorization_url'])
        self.assertEqual(MarketingAuthorizationState.objects.count(), 1)

    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    @patch('identity.views.marketing_credentials_login', return_value={
        'id': '15', 'email': 'member@example.com', 'first_name': 'Member',
        'last_name': 'Example', 'app_access': None,
    })
    def test_common_portal_login_uses_marketing_crm_credentials(self, _login, _services):
        response = self.client.post('/api/auth/marketing/login/', {
            'company_code': self.company.code,
            'email': 'member@example.com',
            'password': 'marketing-password',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertEqual(response.data['company']['code'], self.company.code)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'member@example.com')

    @patch('identity.views.find_salespie_account', return_value=('23', True))
    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    def test_salespie_launch_refreshes_mapping_by_marketing_email(self, _services, _lookup):
        self.user.email = 'member@example.com'
        self.user.save(update_fields=['email'])
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        response = self.client.post('/api/portal/sso/launch/', {'application': 'salespie'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['launch_url'].startswith('http://testserver:8001?sso_code='))
        self.assertIn('sso_code=', response.data['launch_url'])
        self.assertTrue(ApplicationUserMapping.objects.filter(
            user=self.user,
            company=self.company,
            application='salespie',
            external_user_id='23',
            is_active=True,
        ).exists())

    @patch('identity.views.marketing_token_exchange', return_value='15')
    def test_callback_uses_marketing_local_id_not_email(self, _exchange):
        start = self.client.post('/api/auth/marketing/start/', {'company_code': self.company.code}, format='json')
        state = start.data['authorization_url'].split('state=', 1)[1]
        response = self.client.get('/api/auth/marketing/callback/', {'code': 'provider-code', 'state': state})
        self.assertEqual(response.status_code, 302)
        self.assertIn('#auth=access=', response['Location'])
        self.assertIn('company_code=nl-technologies-test', response['Location'])

    @patch('identity.views.marketing_token_exchange', return_value='unmapped-marketing-id')
    def test_callback_does_not_grant_company_access_to_an_unassigned_user(self, _exchange):
        start = self.client.post('/api/auth/marketing/start/', {'company_code': self.company.code}, format='json')
        state = start.data['authorization_url'].split('state=', 1)[1]
        response = self.client.get('/api/auth/marketing/callback/', {'code': 'provider-code', 'state': state})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PortalProfile.objects.filter(
            company=self.company, marketing_user_id='unmapped-marketing-id',
        ).exists())
        self.assertFalse(Membership.objects.filter(
            company=self.company,
            user__portal_profiles__marketing_user_id='unmapped-marketing-id',
        ).exists())

    def test_workspace_always_returns_all_three_tiles_with_entitled_flags(self):
        self.profile.app_access = ['salespie']
        self.profile.save(update_fields=['app_access'])
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        response = self.client.get('/api/portal/workspace/')

        self.assertEqual(response.status_code, 200)
        entitled_by_key = {app['key']: app['entitled'] for app in response.data['applications']}
        self.assertEqual(
            entitled_by_key,
            {'marketing_crm': True, 'salespie': True, 'bdcrm': False},
        )

    def test_workspace_marks_every_tile_entitled_when_app_access_unknown(self):
        self.assertIsNone(self.profile.app_access)
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        response = self.client.get('/api/portal/workspace/')

        self.assertEqual(response.status_code, 200)
        entitled_by_key = {app['key']: app['entitled'] for app in response.data['applications']}
        self.assertEqual(
            entitled_by_key,
            {'marketing_crm': True, 'salespie': True, 'bdcrm': True},
        )

    def test_ensure_portal_identity_updates_app_access_when_specified(self):
        ensure_portal_identity('15', self.company, app_access=['bdcrm'])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.app_access, ['bdcrm'])

    def test_ensure_portal_identity_leaves_app_access_untouched_when_unspecified(self):
        self.profile.app_access = ['salespie']
        self.profile.save(update_fields=['app_access'])

        # This is the exact call shape MarketingLoginCallbackView uses -- the
        # OAuth path this change does not touch.
        ensure_portal_identity('15', self.company)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.app_access, ['salespie'])

    def test_identity_is_scoped_to_company_and_auto_grants_membership(self):
        vbs = Company.objects.create(code='vbs-test', name='VBS Test')
        profile, membership = ensure_portal_identity(
            '15', vbs, email='employee@vbsai.com', app_access=[],
        )

        self.assertIsNotNone(membership)
        self.assertNotEqual(profile.user_id, self.profile.user_id)
        self.assertTrue(Membership.objects.filter(
            user=profile.user, company=vbs, is_active=True,
        ).exists())
        self.assertEqual(profile.user.email, 'employee@vbsai.com')

    @patch('identity.views.urlopen')
    def test_marketing_credentials_login_falls_back_when_app_access_absent(self, mock_urlopen):
        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({'user': {
                    'id': 7, 'email': 'x@example.com', 'firstName': 'X', 'lastName': 'Y',
                }}).encode()

        mock_urlopen.return_value = _FakeResponse()

        result = marketing_credentials_login('x@example.com', 'pw', self.company.id)

        self.assertIsNone(result['app_access'])

    @patch('identity.views.urlopen')
    def test_marketing_credentials_login_parses_app_access_list(self, mock_urlopen):
        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({'user': {
                    'id': 7, 'email': 'x@example.com', 'firstName': 'X', 'lastName': 'Y',
                    'app_access': ['bdcrm', 'salespie'],
                }}).encode()

        mock_urlopen.return_value = _FakeResponse()

        result = marketing_credentials_login('x@example.com', 'pw', self.company.id)

        self.assertEqual(result['app_access'], ['bdcrm', 'salespie'])

    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    @patch('identity.views.find_salespie_account', return_value=('23', False))
    def test_launch_refuses_deactivated_downstream_account(self, _lookup, _services):
        self.user.email = 'member@example.com'
        self.user.save(update_fields=['email'])
        ApplicationUserMapping.objects.create(
            user=self.user, company=self.company, application='salespie',
            external_user_id='23', is_active=True,
        )
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        response = self.client.post('/api/portal/sso/launch/', {'application': 'salespie'}, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'ACCOUNT_NOT_LINKED')
        mapping = ApplicationUserMapping.objects.get(user=self.user, company=self.company, application='salespie')
        self.assertFalse(mapping.is_active)

    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    def test_launch_refuses_a_tile_the_ui_now_renders_but_is_not_entitled(self, _services):
        # WorkspaceView renders all three tiles regardless of entitlement (the
        # muted state is presentation only); SSOLaunchView is still the real
        # authorization boundary. This is the same 'Malavikav' case as the
        # preflight tests below -- a native SalesPie account matched by email
        # that was never granted through Marketing -- but hit at launch time.
        self.profile.app_access = ['bdcrm']
        self.profile.save(update_fields=['app_access'])
        self.user.email = 'member@example.com'
        self.user.save(update_fields=['email'])
        ApplicationUserMapping.objects.create(
            user=self.user, company=self.company, application='salespie',
            external_user_id='23', is_active=True,
        )
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        with patch('identity.views.urlopen') as mock_urlopen:
            response = self.client.post('/api/portal/sso/launch/', {'application': 'salespie'}, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'ACCOUNT_NOT_LINKED')
        # Not entitled means the downstream CRM must never even be probed,
        # cached mapping or not.
        mock_urlopen.assert_not_called()

    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    def test_launch_and_preflight_send_provision_false(self, _services):
        self.user.email = 'member@example.com'
        self.user.save(update_fields=['email'])
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        captured = []

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({
                    'external_user_id': '23', 'created': False,
                    'username': 'member', 'is_active': True,
                }).encode()

        def _fake_urlopen(request, timeout=None):
            captured.append(json.loads(request.data.decode()))
            return _FakeResponse()

        with patch('identity.views.urlopen', side_effect=_fake_urlopen):
            launch_response = self.client.post('/api/portal/sso/launch/', {'application': 'salespie'}, format='json')
            preflight_response = self.client.get('/api/portal/sso/preflight/')

        self.assertEqual(launch_response.status_code, 200)
        self.assertIn('sso_code=', launch_response.data['launch_url'])
        self.assertEqual(preflight_response.status_code, 200)
        self.assertGreaterEqual(len(captured), 3)  # 1 from launch + 2 from preflight
        for body in captured:
            self.assertIs(body.get('provision'), False)

    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    def test_preflight_skips_the_probe_for_an_app_not_in_app_access(self, _services):
        # A real case hit during testing: a user with a pre-existing native
        # SalesPie account (matched by email) who was never granted access
        # through Marketing. Row existence must not read as entitlement, so
        # salespie must be reported not_entitled without even being probed.
        self.profile.app_access = ['bdcrm']
        self.profile.save(update_fields=['app_access'])
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({
                    'external_user_id': '9', 'created': False, 'username': 'm', 'is_active': True,
                }).encode()

        with patch('identity.views.urlopen', return_value=_FakeResponse()) as mock_urlopen:
            response = self.client.get('/api/portal/sso/preflight/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['applications']['salespie']['status'], 'not_entitled')
        self.assertEqual(response.data['applications']['bdcrm']['status'], 'entitled')
        # Only the one bdcrm probe should have gone out -- salespie was
        # excluded by app_access before any request was built.
        self.assertEqual(mock_urlopen.call_count, 1)
        called_request = mock_urlopen.call_args[0][0]
        self.assertEqual(called_request.full_url, settings.BDCRM_ACCOUNT_LOOKUP_URL)

    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    def test_preflight_reports_not_provisioned_when_entitled_but_deactivated(self, _services):
        self.profile.app_access = ['salespie']
        self.profile.save(update_fields=['app_access'])
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({
                    'external_user_id': '9', 'created': False, 'username': 'm', 'is_active': False,
                }).encode()

        with patch('identity.views.urlopen', return_value=_FakeResponse()):
            response = self.client.get('/api/portal/sso/preflight/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['applications']['salespie']['status'], 'not_provisioned')
