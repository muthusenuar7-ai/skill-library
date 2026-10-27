"""DEVELOPMENT ONLY: give the five initial management accounts temporary passwords so they can sign in locally.

Temporary passwords must be supplied through private DEV_ADMIN_PASSWORD_<FIRSTNAME> environment variables.
Every account is forced to choose its own password at first login. The command refuses to run unless Django is a
verified local development instance, never prints a password, and never overwrites a password its owner has already
changed unless --reset is given.
"""
import os

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.management.base import BaseCommand, CommandError

from accounts.models import ROLE_ADMIN, ROLE_LABELS, ROLE_SUPER, User
from accounts.roles import ensure_roles, set_role
from library.activity import log_activity

ACCOUNTS = [
    ("muthu@datasense.com", "Muthu", ROLE_SUPER),
    ("vineet@datasense.com", "Vineet", ROLE_SUPER),
    ("akhilesh@datasense.com", "Akhilesh", ROLE_ADMIN),
    ("himanshi@datasense.com", "Himanshi", ROLE_ADMIN),
    ("amit@datasense.com", "Amit", ROLE_ADMIN),
]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}


def temporary_password(first_name):
    value = os.environ.get(f"DEV_ADMIN_PASSWORD_{first_name.upper()}", "")
    if len(value) < 12:
        raise CommandError("Set private DEV_ADMIN_PASSWORD_<FIRSTNAME> environment variables (at least 12 characters). No default passwords are provided.")
    return value


def assert_local_development():
    problems = []
    if not settings.DEBUG:
        problems.append("DEBUG is off")
    if getattr(settings, "ENVIRONMENT", "") != "development":
        problems.append(f"DJANGO_ENV is {getattr(settings, 'ENVIRONMENT', '?')!r}, not 'development'")
    if not set(settings.ALLOWED_HOSTS) <= LOCAL_HOSTS:
        problems.append("ALLOWED_HOSTS contains a non-local host")
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        problems.append("the database is not the local SQLite development database")
    if problems:
        raise CommandError("Refusing to run: this is not a verified local development environment (" + "; ".join(problems) + ").")


class Command(BaseCommand):
    help = "Development only. Create/prepare the five initial management accounts with temporary passwords (forced change at first login)."

    def add_arguments(self, parser):
        parser.add_argument("--development-only", action="store_true", help="Required. Confirms you understand this sets locally supplied temporary passwords.")
        parser.add_argument("--reset", action="store_true", help="Also reset accounts whose owner has already chosen their own password.")

    def handle(self, *args, **opts):
        if not opts["development_only"]:
            raise CommandError("Pass --development-only to confirm this is a local development database.")
        assert_local_development()
        # Validate the whole configuration before touching any accounts.
        for _, first, _ in ACCOUNTS:
            temporary_password(first)
        ensure_roles()
        report, failures = [], 0
        for email, first, role in ACCOUNTS:
            user = User.objects.filter(email__iexact=email).first()
            created = user is None
            if created:
                user = User(email=email, full_name=first)
                user.set_unusable_password()
                user.save()
            changed_by_owner = user.has_usable_password() and not user.must_change_password
            if not user.full_name:
                user.full_name = first
            user.is_active, user.is_superuser, user.is_staff = True, False, False
            if changed_by_owner and not opts["reset"]:
                user.save()
                set_role(user, role)
                report.append((email, ROLE_LABELS[role], "kept", "owner already chose a password (use --reset to override)", "not tested"))
                continue
            user.set_password(temporary_password(first))  # Django's PBKDF2 hasher - never stored in plain text
            user.must_change_password = True
            user.save()
            set_role(user, role)
            ok = authenticate(username=email, password=temporary_password(first))
            good = ok is not None and ok.pk == user.pk and ok.role == role and ok.is_active and ok.has_admin_access
            failures += not good
            state = "created" if created else ("reset" if changed_by_owner else "initialized")
            report.append((email, ROLE_LABELS[role], state, "temporary password set; change required at first login", "OK" if good else "FAILED"))
            log_activity(None, "dev_password_init", "user", user.pk, f"{state} {email} with a temporary development password")
        widths = [max(len(str(r[i])) for r in report + [("Account", "Role", "State", "Details", "Login test")]) for i in range(5)]
        for r in [("Account", "Role", "State", "Details", "Login test")] + report:
            self.stdout.write("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
        if failures:
            raise CommandError(f"{failures} account(s) failed the login verification.")
        self.stdout.write(self.style.SUCCESS("\nDone. Sign in at /admin/login/ - each user is asked to choose a new password immediately."))
        self.stdout.write("Temporary passwords are for local development only: never use this command or these passwords on a deployed site.")
