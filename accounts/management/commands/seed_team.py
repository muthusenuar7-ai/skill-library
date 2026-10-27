from django.contrib.auth.tokens import default_token_generator
from django.core.management.base import BaseCommand
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import ROLE_ADMIN, ROLE_SUPER, User
from accounts.roles import ensure_roles, set_role

SUPER_ADMINS = ["muthu@datasense.com", "vineet@datasense.com"]
ADMINS = ["akhilesh@datasense.com", "himanshi@datasense.com", "amit@datasense.com"]


def activation_path(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return f"/admin/activate/{uid}/{default_token_generator.make_token(user)}/"


class Command(BaseCommand):
    help = (
        "Create the initial team accounts WITHOUT passwords and print one-time activation links. "
        "Safe to run again: existing accounts keep their passwords; only users who have not yet set "
        "a password get a fresh link."
    )

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Site address used in the printed links.")

    def handle(self, *args, **options):
        ensure_roles()
        base = options["base_url"].rstrip("/")
        self.stdout.write("Team accounts (links stop working once the password is set or after the timeout):\n")
        for role, emails in ((ROLE_SUPER, SUPER_ADMINS), (ROLE_ADMIN, ADMINS)):
            for email in emails:
                user, created = User.objects.get_or_create(email=email, defaults={"is_active": True})
                if created:
                    user.set_unusable_password()
                    user.save(update_fields=["password"])
                set_role(user, role)
                state = "created" if created else "exists"
                self.stdout.write(f"- {email}  [{role}, {state}]")
                if not user.has_usable_password():
                    self.stdout.write(f"    activation link: {base}{activation_path(user)}")
                else:
                    self.stdout.write("    password already set")
        self.stdout.write(self.style.SUCCESS("\nDone. Give each link only to its owner; do not paste links into shared chats or commit them."))
