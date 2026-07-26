from django.db import migrations


def seed_companies(apps, schema_editor):
    Company = apps.get_model('identity', 'Company')
    Company.objects.get_or_create(code='nl-technologies', defaults={'name': 'NL Technologies'})
    Company.objects.get_or_create(code='vbs', defaults={'name': 'VBS'})


class Migration(migrations.Migration):
    dependencies = [('identity', '0001_initial')]
    operations = [migrations.RunPython(seed_companies, migrations.RunPython.noop)]
