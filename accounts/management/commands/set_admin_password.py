import getpass

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Set an account password from a hidden terminal prompt (nothing is echoed or stored in history)."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email__iexact=options["email"].strip())
        except User.DoesNotExist:
            raise CommandError("No such account. Run `python manage.py seed_team` first.")
        pw1 = getpass.getpass("New password: ")
        pw2 = getpass.getpass("Repeat password: ")
        if pw1 != pw2:
            raise CommandError("Passwords do not match.")
        try:
            password_validation.validate_password(pw1, user)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        user.set_password(pw1)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        self.stdout.write(self.style.SUCCESS(f"Password set for {user.email}."))
