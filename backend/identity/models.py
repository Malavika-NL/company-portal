from django.conf import settings
from django.db import models


DEFAULT_COMPANIES = (
    ('nl-technologies', 'NL Technologies'),
    ('vbs', 'VBS'),
)


class Company(models.Model):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


def ensure_default_companies():
    """Create the organizations displayed by the public portal, if missing."""
    for code, name in DEFAULT_COMPANIES:
        # Existing records are intentionally not changed, so an administrator's
        # name or active-status changes are preserved.
        Company.objects.get_or_create(code=code, defaults={'name': name})


class Membership(models.Model):
    ROLE_CHOICES = [('platform_admin', 'Platform admin'), ('admin', 'Admin'), ('member', 'Member')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='portal_memberships')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'company'], name='unique_portal_membership')]


class PortalProfile(models.Model):
    """Portal-side identity linked to one immutable Marketing CRM user ID.

    This deliberately contains no password, password hash, or Marketing email.
    Marketing CRM is the only credential authority.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='portal_profile')
    marketing_user_id = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=True)
    # Cached from Marketing's login response (user.app_access)
    app_access = models.JSONField(null=True, blank=True, default=None)

    def __str__(self):
        return f'Marketing CRM user {self.marketing_user_id}'


class ApplicationUserMapping(models.Model):
    # Marketing CRM is the identity provider and is mapped by PortalProfile.
    # These mappings are only for downstream CRM local account IDs.
    APPLICATIONS = [('salespie', 'SalesPie'), ('bdcrm', 'BDCRM')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='application_mappings')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='user_mappings')
    application = models.CharField(max_length=32, choices=APPLICATIONS)
    external_user_id = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'company', 'application'], name='unique_portal_app_mapping'),
            models.UniqueConstraint(fields=['company', 'application', 'external_user_id'], name='unique_external_app_mapping'),
        ]


class SSOCode(models.Model):
    APPLICATIONS = [('marketing_crm', 'Marketing CRM'), ('salespie', 'SalesPie'), ('bdcrm', 'BDCRM')]
    code_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    application = models.CharField(max_length=32, choices=APPLICATIONS)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MarketingAuthorizationState(models.Model):
    """Single-use correlation value for the Marketing authorization-code redirect."""
    state_hash = models.CharField(max_length=64, unique=True)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

# Create your models here.
