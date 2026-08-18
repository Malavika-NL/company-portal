from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


def scope_unambiguous_profiles(apps, schema_editor):
    """Retain legacy rows without allowing them to bridge companies.

    A row with exactly one membership can be safely assigned to that company.
    Rows with no or multiple memberships remain unscoped and are ignored by
    runtime queries, avoiding an implicit cross-company data transfer.
    """
    PortalProfile = apps.get_model('identity', 'PortalProfile')
    Membership = apps.get_model('identity', 'Membership')
    for profile in PortalProfile.objects.filter(company__isnull=True).iterator():
        company_ids = list(Membership.objects.filter(
            user_id=profile.user_id, is_active=True,
        ).values_list('company_id', flat=True)[:2])
        if len(company_ids) == 1:
            profile.company_id = company_ids[0]
            profile.save(update_fields=['company'])


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0006_remove_member_role'),
    ]

    operations = [
        migrations.AlterField(
            model_name='portalprofile',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='portal_profiles',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='portalprofile',
            name='company',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='portal_profiles',
                to='identity.company',
            ),
        ),
        migrations.AlterField(
            model_name='portalprofile',
            name='marketing_user_id',
            field=models.CharField(max_length=128),
        ),
        migrations.RunPython(scope_unambiguous_profiles, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='portalprofile',
            constraint=models.UniqueConstraint(
                fields=('company', 'marketing_user_id'),
                name='unique_company_marketing_user',
            ),
        ),
    ]
