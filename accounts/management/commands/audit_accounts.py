from django.core.management.base import BaseCommand

from accounts.management.commands.seed_team import ADMINS, SUPER_ADMINS
from accounts.models import ROLE_ADMIN, ROLE_LABELS, ROLE_SUPER, User

EXPECTED = [(e, ROLE_SUPER) for e in SUPER_ADMINS] + [(e, ROLE_ADMIN) for e in ADMINS]
MUST_HAVE = {
    ROLE_ADMIN: ["library.view_skill", "library.add_skill", "library.change_skill", "library.publish_skill", "library.import_skills"],
    ROLE_SUPER: ["library.view_skill", "library.add_skill", "library.change_skill", "library.publish_skill", "library.import_skills",
                 "library.change_category", "accounts.manage_team", "accounts.manage_settings", "accounts.export_library"],
}
MUST_NOT_HAVE = {ROLE_ADMIN: ["accounts.manage_team", "accounts.manage_settings", "accounts.export_library", "library.change_category"]}


class Command(BaseCommand):
    help = (
        "Read-only report on the five initial management accounts: exists, role, active, usable password configured, "
        "permissions correct. Never reads, prints or changes any password."
    )

    def handle(self, *args, **opts):
        rows, problems = [], 0
        for email, want in EXPECTED:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                rows.append((email, ROLE_LABELS[want], "MISSING", "-", "-", "-", "run: python manage.py seed_team"))
                problems += 1
                continue
            has_pw = user.has_usable_password()
            perms_ok = all(user.has_perm(p) for p in MUST_HAVE[want]) and not any(user.has_perm(p) for p in MUST_NOT_HAVE.get(want, []))
            flags_ok = not user.is_superuser and not user.is_staff
            role_ok = user.role == want
            notes = []
            if not role_ok:
                notes.append(f"role is {user.role_label}, expected {ROLE_LABELS[want]} (run seed_team to correct)")
            if not user.is_active:
                notes.append("deactivated")
            if not has_pw:
                notes.append("no password yet: use the activation link from seed_team, or initialize_admin_passwords --development-only on a local PC")
            elif user.must_change_password:
                notes.append("temporary password in use - must be changed at first login")
            if not perms_ok:
                notes.append("permissions differ from the role definition")
            if not flags_ok:
                notes.append("has Django superuser/staff flags")
            problems += bool(notes)
            rows.append((email, user.role_label, "yes", "yes" if user.is_active else "NO", "yes" if has_pw else "NO", "yes" if perms_ok and role_ok and flags_ok else "NO", "; ".join(notes) or "ok"))
        header = ("Account", "Role", "Exists", "Active", "Password set", "Permissions OK", "Notes")
        widths = [max(len(str(r[i])) for r in [header] + rows) for i in range(7)]
        for r in [header] + rows:
            self.stdout.write("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
        others = User.objects.exclude(email__in=[e for e, _ in EXPECTED]).count()
        self.stdout.write(f"\nOther accounts in the database: {others}")
        self.stdout.write(self.style.SUCCESS("All five accounts are in order.") if not problems else self.style.WARNING(f"{problems} account(s) need attention."))
