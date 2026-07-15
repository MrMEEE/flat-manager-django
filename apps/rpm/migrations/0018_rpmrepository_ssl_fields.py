from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0017_rpmrepository_gpgkey'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmrepository',
            name='sslcacert',
            field=models.TextField(
                blank=True,
                help_text='Path to CA certificate used to verify the server (e.g. /etc/rhsm/ca/katello-server-ca.pem)',
            ),
        ),
        migrations.AddField(
            model_name='rpmrepository',
            name='sslclientcert',
            field=models.TextField(
                blank=True,
                help_text='Path to client certificate for mutual TLS (RHSM/Satellite entitlement cert)',
            ),
        ),
        migrations.AddField(
            model_name='rpmrepository',
            name='sslclientkey',
            field=models.TextField(
                blank=True,
                help_text='Path to client private key for mutual TLS (RHSM/Satellite entitlement key)',
            ),
        ),
    ]
