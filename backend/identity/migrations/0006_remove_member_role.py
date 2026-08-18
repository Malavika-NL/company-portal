from django.db import migrations, models


def replace_member_role(apps, schema_editor):
    Membership = apps.get_model('identity', 'Membership')
    Membership.objects.filter(role='member').update(role='user')


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0005_portalprofile_app_access'),
    ]

    operations = [
        migrations.RunPython(replace_member_role, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='membership',
            name='role',
            field=models.CharField(
                choices=[
                    ('platform_admin', 'Platform admin'),
                    ('admin', 'Admin'),
                    ('user', 'User'),
                ],
                default='user',
                max_length=20,
            ),
        ),
    ]
