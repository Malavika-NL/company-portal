from django.db import migrations


def attach_orphaned_profiles_to_explicit_memberships(apps, schema_editor):
    """Repair profiles created by a rejected login during the 0007 rollout."""
    PortalProfile = apps.get_model('identity', 'PortalProfile')
    Membership = apps.get_model('identity', 'Membership')

    for legacy in PortalProfile.objects.filter(company__isnull=True).iterator():
        for membership in Membership.objects.filter(user_id=legacy.user_id, is_active=True).iterator():
            scoped = PortalProfile.objects.filter(
                company_id=membership.company_id,
                marketing_user_id=legacy.marketing_user_id,
            ).first()
            if scoped and scoped.user_id != membership.user_id:
                # A scoped profile without a membership is an unassigned row
                # from a failed login, not an approved identity for this
                # tenant. Bind it to the already-approved membership instead.
                has_access = Membership.objects.filter(
                    user_id=scoped.user_id, company_id=membership.company_id,
                    is_active=True,
                ).exists()
                if not has_access:
                    scoped.user_id = membership.user_id
                    scoped.save(update_fields=['user'])
            elif not scoped:
                PortalProfile.objects.create(
                    user_id=membership.user_id,
                    company_id=membership.company_id,
                    marketing_user_id=legacy.marketing_user_id,
                    is_active=legacy.is_active,
                    app_access=legacy.app_access,
                )


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0008_scope_explicit_legacy_memberships'),
    ]

    operations = [
        migrations.RunPython(attach_orphaned_profiles_to_explicit_memberships, migrations.RunPython.noop),
    ]
