from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import ApplicationUserMapping, Company, MarketingAuthorizationState, Membership, PortalProfile


class MarketingProviderLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(code='nl-technologies-test', name='NL Technologies Test')
        self.user = User.objects.create_user(username='portal-identity', password='legacy-password')
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        PortalProfile.objects.create(user=self.user, marketing_user_id='15')
        Membership.objects.create(user=self.user, company=self.company)

    @patch('identity.views.warm_application_services')
    def test_companies_endpoint_initializes_default_companies(self, warm_services):
        Company.objects.all().delete()

        response = self.client.get('/api/companies/')

        warm_services.assert_called_once()
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
    @patch('identity.views.marketing_credentials_login', return_value='15')
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

    @patch('identity.views.find_salespie_account', return_value='23')
    @patch('identity.views.ensure_application_services', return_value=(True, ''))
    def test_salespie_launch_refreshes_mapping_by_marketing_email(self, _services, _lookup):
        self.user.email = 'member@example.com'
        self.user.save(update_fields=['email'])
        self.client.force_authenticate(user=self.user, token={'company_id': self.company.id})

        response = self.client.post('/api/portal/sso/launch/', {'application': 'salespie'}, format='json')

        self.assertEqual(response.status_code, 200)
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
    def test_callback_creates_a_portal_link_for_a_verified_marketing_user(self, _exchange):
        start = self.client.post('/api/auth/marketing/start/', {'company_code': self.company.code}, format='json')
        state = start.data['authorization_url'].split('state=', 1)[1]
        response = self.client.get('/api/auth/marketing/callback/', {'code': 'provider-code', 'state': state})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PortalProfile.objects.filter(marketing_user_id='unmapped-marketing-id').exists())
