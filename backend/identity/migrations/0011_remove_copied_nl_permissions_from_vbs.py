from django.db import migrations


def remove_copied_permissions(apps, schema_editor):
    """Ensure the NL-to-VBS transfer contains user identity only.

    Matching user IDs are expected because users are intentionally copied.
    Roles and application grants are separate company data, so copied VBS
    profiles start with no downstream application access.
    """
    Company = apps.get_model('identity', 'Company')
    Membership = apps.get_model('identity', 'Membership')
    PortalProfile = apps.get_model('identity', 'PortalProfile')

    nl_company = Company.objects.filter(code='nl-technologies').first()
    vbs_company = Company.objects.filter(code='vbs').first()
    if not nl_company or not vbs_company:
        return

    for nl_profile in PortalProfile.objects.filter(company_id=nl_company.id, is_active=True).iterator():
        vbs_profile = PortalProfile.objects.filter(
            company_id=vbs_company.id,
            user_id=nl_profile.user_id,
            marketing_user_id=nl_profile.marketing_user_id,
            is_active=True,
        ).first()
        # Equal values indicate the previous migration copied an NL grant.
        # Clear it instead of carrying it into VBS. Existing differing VBS
        # permissions remain VBS-owned data and are not touched.
        if vbs_profile and vbs_profile.app_access == nl_profile.app_access:
            vbs_profile.app_access = []
            vbs_profile.save(update_fields=['app_access'])

        membership = Membership.objects.filter(
            user_id=nl_profile.user_id, company_id=vbs_company.id, is_active=True,
        ).first()
        if membership and membership.role == Membership.objects.filter(
            user_id=nl_profile.user_id, company_id=nl_company.id,
        ).values_list('role', flat=True).first():
            membership.role = 'user'
            membership.save(update_fields=['role'])


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0010_copy_nl_users_to_vbs'),
    ]

    operations = [
        migrations.RunPython(remove_copied_permissions, migrations.RunPython.noop),
    ]
