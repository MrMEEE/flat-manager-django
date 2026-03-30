import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Set the password for a user account.'

    def add_arguments(self, parser):
        parser.add_argument('username', help='Username of the account to update.')
        parser.add_argument(
            '--password',
            dest='password',
            default=None,
            help='New password. If omitted, you will be prompted interactively.',
        )

    def handle(self, *args, **options):
        from apps.users.models import User

        username = options['username']
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' does not exist.")

        password = options['password']
        if password is None:
            password = getpass.getpass(f'New password for {username}: ')
            confirm = getpass.getpass('Confirm password: ')
            if password != confirm:
                raise CommandError('Passwords do not match.')

        try:
            validate_password(password, user=user)
        except ValidationError as e:
            raise CommandError('\n'.join(e.messages))

        user.set_password(password)
        user.save(update_fields=['password'])
        self.stdout.write(self.style.SUCCESS(f"Password updated for '{username}'."))
