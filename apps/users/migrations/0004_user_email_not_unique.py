from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_ldapsource_attr_mapping'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='email',
            field=models.EmailField(blank=True, max_length=254, verbose_name='email address'),
        ),
    ]
