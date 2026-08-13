from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0004_alter_applicationusermapping_application'),
    ]

    operations = [
        migrations.AddField(
            model_name='portalprofile',
            name='app_access',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
