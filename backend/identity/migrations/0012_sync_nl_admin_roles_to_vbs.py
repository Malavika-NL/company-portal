from django.db import migrations


def sync_shared_user_roles(apps, schema_editor):
    """Keep authorization roles with the shared NL -> VBS user identities."""
    Company = apps.get_model('identity', 'Company')
    Membership = apps.get_model('identity', 'Membership')

    nl_company = Company.objects.filter(code='nl-technologies').first()
    vbs_company = Company.objects.filter(code='vbs').first()
    if not nl_company or not vbs_company:
        return

    for nl_membership in Membership.objects.filter(company_id=nl_company.id, is_active=True):
        vbs_membership = Membership.objects.filter(
            company_id=vbs_company.id,
            user_id=nl_membership.user_id,
            is_active=True,
        ).first()
        # Never overwrite a VBS-local role.  The only allowed cross-company
        # role action is preserving an already-authorized NL administrator.
        if vbs_membership and nl_membership.role == 'admin' and vbs_membership.role != 'admin':
            vbs_membership.role = 'admin'
            vbs_membership.save(update_fields=['role'])


class Migration(migrations.Migration):
    dependencies = [('identity', '0011_remove_copied_nl_permissions_from_vbs')]

    operations = [migrations.RunPython(sync_shared_user_roles, migrations.RunPython.noop)]
