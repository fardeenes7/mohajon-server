import django.contrib.postgres.operations
from django.db import migrations

class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        django.contrib.postgres.operations.CreateExtension('vector'),
    ]
