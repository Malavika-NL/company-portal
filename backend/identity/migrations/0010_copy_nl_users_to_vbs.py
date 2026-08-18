from django.db import migrations


NL_COMPANY_CODE = 'nl-technologies'
VBS_COMPANY_CODE = 'vbs'


def copy_active_nl_users_to_vbs(apps, schema_editor):
    """Give every active NL portal user an equivalent VBS user assignment.

    This is an explicit, one-way user-only transfer. It creates no contact,
    CRM business, attachment, configuration, or application-account data.
    """
    Company = apps.get_model('identity', 'Company')
    Membership = apps.get_model('identity', 'Membership')
    PortalProfile = apps.get_model('identity', 'PortalProfile')

    nl_company = Company.objects.filter(code=NL_COMPANY_CODE).first()
    vbs_company = Company.objects.filter(code=VBS_COMPANY_CODE).first()
    if not nl_company or not vbs_company:
        return

    nl_memberships = Membership.objects.filter(company_id=nl_company.id, is_active=True)
    for membership in nl_memberships.iterator():
        vbs_membership, created = Membership.objects.get_or_create(
            user_id=membership.user_id,
            company_id=vbs_company.id,
            # Roles are company data, not transferable user identity data.
            defaults={'role': 'user', 'is_active': True},
        )
        if not created and not vbs_membership.is_active:
            # An explicit VBS deactivation remains authoritative.
            continue

        for profile in PortalProfile.objects.filter(
            user_id=membership.user_id,
            company_id=nl_company.id,
            is_active=True,
        ).iterator():
            # A VBS profile already associated with this CRM ID wins; never
            # overwrite a VBS-local identity or its application permissions.
            PortalProfile.objects.get_or_create(
                company_id=vbs_company.id,
                marketing_user_id=profile.marketing_user_id,
                defaults={
                    'user_id': membership.user_id,
                    'is_active': True,
                    # VBS application permissions must be configured in VBS.
                    'app_access': [],
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0009_repair_orphaned_scoped_profiles'),
    ]

    operations = [
        migrations.RunPython(copy_active_nl_users_to_vbs, migrations.RunPython.noop),
    ]
