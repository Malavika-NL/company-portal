from django.db import migrations


def create_profiles_for_explicit_memberships(apps, schema_editor):
    """Split legacy shared profiles only where access was already explicit.

    The old schema allowed one profile to sit behind memberships in multiple
    companies. Each existing active membership is an administrator-approved
    assignment, so it receives its own company-scoped profile. The old
    unscoped row is disabled and is never used as a fallback.
    """
    PortalProfile = apps.get_model('identity', 'PortalProfile')
    Membership = apps.get_model('identity', 'Membership')

    for profile in PortalProfile.objects.filter(company__isnull=True).iterator():
        memberships = Membership.objects.filter(user_id=profile.user_id, is_active=True)
        for membership in memberships.iterator():
            # The target company may already have this CRM-local ID attached
            # to a distinct legacy portal user.  It wins; do not merge users
            # or overwrite that existing company-scoped identity.
            PortalProfile.objects.get_or_create(
                company_id=membership.company_id,
                marketing_user_id=profile.marketing_user_id,
                defaults={
                    'user_id': profile.user_id,
                    'is_active': profile.is_active,
                    'app_access': profile.app_access,
                },
            )
        profile.is_active = False
        profile.save(update_fields=['is_active'])


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0007_company_scoped_portal_profiles'),
    ]

    operations = [
        migrations.RunPython(create_profiles_for_explicit_memberships, migrations.RunPython.noop),
    ]
