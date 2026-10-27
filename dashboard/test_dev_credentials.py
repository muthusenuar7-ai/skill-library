from io import StringIO
import os
import secrets
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.management.commands.initialize_admin_passwords import ACCOUNTS, temporary_password
from accounts.models import User

DEV = override_settings(DEBUG=True, ENVIRONMENT="development")
SPEC = {  # e-mail -> (name, role label, temporary password) generated only inside the disposable test process
    "muthu@datasense.com": ("Muthu", "Super Admin", secrets.token_urlsafe(24)),
    "vineet@datasense.com": ("Vineet", "Super Admin", secrets.token_urlsafe(24)),
    "akhilesh@datasense.com": ("Akhilesh", "Admin", secrets.token_urlsafe(24)),
    "himanshi@datasense.com": ("Himanshi", "Admin", secrets.token_urlsafe(24)),
    "amit@datasense.com": ("Amit", "Admin", secrets.token_urlsafe(24)),
}
NEW_PASSWORD = secrets.token_urlsafe(32)


def run(*args):
    out = StringIO()
    call_command("initialize_admin_passwords", *args, stdout=out)
    return out.getvalue()


class InitializeCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        credentials = patch.dict(os.environ, {"DEV_ADMIN_PASSWORD_" + name.upper(): pw for name, _, pw in SPEC.values()})
        credentials.start()
        self.addCleanup(credentials.stop)

    def test_requires_the_explicit_flag(self):
        with DEV, self.assertRaises(CommandError):
            run()
        self.assertEqual(User.objects.count(), 0)

    @DEV
    def test_missing_private_passwords_changes_no_accounts(self):
        with patch.dict(os.environ, {"DEV_ADMIN_PASSWORD_MUTHU": ""}):
            with self.assertRaisesMessage(CommandError, "No default passwords"):
                run("--development-only")
        self.assertEqual(User.objects.count(), 0)

    def test_refuses_outside_local_development(self):
        for ov in (override_settings(DEBUG=False, ENVIRONMENT="development"), override_settings(DEBUG=True, ENVIRONMENT="production"),
                   override_settings(DEBUG=True, ENVIRONMENT="development", ALLOWED_HOSTS=["skills.example.com"])):
            with ov, self.assertRaises(CommandError) as cm:
                run("--development-only")
            self.assertIn("Refusing to run", str(cm.exception))
        self.assertEqual(User.objects.count(), 0)  # nothing was created or changed

    @DEV
    def test_creates_exactly_the_five_accounts_with_correct_roles_and_flags(self):
        out = run("--development-only")
        self.assertEqual(User.objects.count(), 5)
        for email, (name, role, _pw) in SPEC.items():
            u = User.objects.get(email=email)
            self.assertEqual((u.full_name, u.role_label, u.is_active), (name, role, True))
            self.assertEqual((u.is_superuser, u.is_staff, u.must_change_password), (False, False, True))
            self.assertTrue(u.has_admin_access)
        self.assertEqual(out.count("OK"), 5)
        self.assertEqual([a[0] for a in ACCOUNTS], list(SPEC))

    @DEV
    def test_permissions_match_the_role(self):
        run("--development-only")
        for email, (_n, role, _p) in SPEC.items():
            u = User.objects.get(email=email)
            sup = role == "Super Admin"
            self.assertEqual(u.has_perm("accounts.manage_team"), sup, email)
            self.assertEqual(u.has_perm("accounts.export_library"), sup, email)
            self.assertTrue(u.has_perm("library.publish_skill") and u.has_perm("library.add_skill") and u.has_perm("library.import_skills"), email)

    @DEV
    def test_passwords_work_through_django_authentication_and_are_hashed(self):
        run("--development-only")
        for email, (_n, _r, pw) in SPEC.items():
            self.assertEqual(temporary_password(SPEC[email][0]), pw)
            user = authenticate(username=email, password=pw)
            self.assertIsNotNone(user, email)
            self.assertIsNone(authenticate(username=email, password=pw + "x"))
            stored = User.objects.get(email=email).password
            self.assertNotIn(pw, stored)  # never stored as plain text
            self.assertRegex(stored, r"^[a-z0-9_]+\$")  # Django hasher format

    @DEV
    def test_output_never_prints_a_password(self):
        out = run("--development-only")
        for _n, _r, pw in SPEC.values():
            self.assertNotIn(pw, out)
        self.assertNotIn("DEV_ADMIN_PASSWORD_", out)

    @DEV
    def test_idempotent_and_does_not_duplicate(self):
        run("--development-only")
        before = {u.email: u.password for u in User.objects.all()}
        again = run("--development-only")  # temporary passwords still in use -> safe to re-apply
        self.assertEqual(User.objects.count(), 5)
        self.assertEqual(User.objects.filter(email__iexact="MUTHU@datasense.com").count(), 1)
        self.assertTrue(all(User.objects.get(email=e).must_change_password for e in before))
        self.assertIn("initialized", again)

    @DEV
    def test_existing_accounts_are_updated_not_duplicated_and_case_insensitive(self):
        User.objects.create_user("MUTHU@Datasense.com", full_name="")  # pre-existing, unusable password
        run("--development-only")
        self.assertEqual(User.objects.count(), 5)
        self.assertEqual(User.objects.get(email="muthu@datasense.com").full_name, "Muthu")

    @DEV
    def test_does_not_overwrite_a_password_the_owner_chose_unless_reset(self):
        run("--development-only")
        u = User.objects.get(email="amit@datasense.com")
        u.set_password(NEW_PASSWORD)
        u.must_change_password = False
        u.save()
        out = run("--development-only")
        self.assertIn("kept", out)
        self.assertTrue(User.objects.get(email="amit@datasense.com").check_password(NEW_PASSWORD))
        self.assertIsNone(authenticate(username="amit@datasense.com", password=SPEC["amit@datasense.com"][2]))
        out = run("--development-only", "--reset")
        self.assertIn("reset", out)
        self.assertIsNotNone(authenticate(username="amit@datasense.com", password=SPEC["amit@datasense.com"][2]))
        self.assertTrue(User.objects.get(email="amit@datasense.com").must_change_password)

    @DEV
    def test_reactivates_and_strips_framework_privileges(self):
        u = User.objects.create_user("vineet@datasense.com", is_active=False, is_staff=True, is_superuser=True)
        run("--development-only")
        u.refresh_from_db()
        self.assertEqual((u.is_active, u.is_staff, u.is_superuser, u.role), (True, False, False, "super_admin"))

    def test_no_public_url_can_reset_passwords(self):
        from django.urls import get_resolver
        names = " ".join(str(p.pattern) + str(getattr(p, "name", "")) for p in get_resolver().url_patterns)
        self.assertNotIn("initialize", names)
        self.assertNotIn("dev-password", names)


@DEV
class FirstLoginFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        credentials = patch.dict(os.environ, {"DEV_ADMIN_PASSWORD_" + name.upper(): pw for name, _, pw in SPEC.values()})
        credentials.start()
        self.addCleanup(credentials.stop)
        run("--development-only")

    def login(self, email, password):
        return self.client.post("/admin/login/", {"email": email, "password": password})

    def test_all_five_accounts_log_in_and_are_forced_to_change_the_password(self):
        for email, (_n, role, pw) in SPEC.items():
            self.client.logout()
            r = self.login(email, pw)
            self.assertRedirects(r, "/admin/change-password/", fetch_redirect_response=False)
            page = self.client.get("/admin/change-password/")
            self.assertContains(page, "Choose your own password")
            self.assertContains(page, email)

    def test_wrong_password_is_rejected_generically(self):
        r = self.login("muthu@datasense.com", "Muthu@000000")
        self.assertContains(r, "Incorrect e-mail address or password")

    def test_dashboard_and_every_management_url_are_blocked_until_the_password_is_changed(self):
        self.login("akhilesh@datasense.com", SPEC["akhilesh@datasense.com"][2])
        for url in ("/admin/dashboard/", "/admin/skills/", "/admin/skills/new/", "/admin/import/", "/admin/categories/", "/admin/activity/", "/admin/team/", "/django-admin/"):
            r = self.client.get(url)
            self.assertRedirects(r, "/admin/change-password/", fetch_redirect_response=False, msg_prefix=url)
        self.assertRedirects(self.client.post("/admin/skills/bulk/", {"action": "publish", "ids": "1"}), "/admin/change-password/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/").status_code, 200)  # the public site is unaffected
        self.assertEqual(self.client.post("/admin/logout/").status_code, 302)  # sign out stays possible

    def change(self, old, new, confirm=None):
        return self.client.post("/admin/change-password/", {"old_password": old, "new_password1": new, "new_password2": confirm or new})

    def test_change_requires_correct_old_matching_strong_and_different_new_password(self):
        self.login("himanshi@datasense.com", SPEC["himanshi@datasense.com"][2])
        self.assertContains(self.change("wrong-old-password", NEW_PASSWORD), "incorrect")
        self.assertContains(self.change(SPEC["himanshi@datasense.com"][2], NEW_PASSWORD, "Different-Confirm-1!"), "didn")
        self.assertContains(self.change(SPEC["himanshi@datasense.com"][2], "short"), "too short")
        self.assertContains(self.change(SPEC["himanshi@datasense.com"][2], SPEC["himanshi@datasense.com"][2]), "different from your current")
        self.assertContains(self.change(SPEC["himanshi@datasense.com"][2], "123456789012"), "entirely numeric")
        self.assertTrue(User.objects.get(email="himanshi@datasense.com").must_change_password)  # still locked

    def test_successful_change_unlocks_the_dashboard_and_kills_the_old_password(self):
        self.login("muthu@datasense.com", SPEC["muthu@datasense.com"][2])
        r = self.change(SPEC["muthu@datasense.com"][2], NEW_PASSWORD)
        self.assertRedirects(r, "/admin/dashboard/", fetch_redirect_response=False)
        u = User.objects.get(email="muthu@datasense.com")
        self.assertFalse(u.must_change_password)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 200)  # session survived the change
        self.client.logout()
        self.assertContains(self.login("muthu@datasense.com", SPEC["muthu@datasense.com"][2]), "Incorrect e-mail address or password")  # old password rejected
        self.assertRedirects(self.login("muthu@datasense.com", NEW_PASSWORD), "/admin/dashboard/", fetch_redirect_response=False)

    def test_roles_are_applied_automatically_after_the_change(self):
        for email, (_n, role, pw) in SPEC.items():
            self.client.logout()
            self.login(email, pw)
            self.change(pw, NEW_PASSWORD + email[:1])
            dash = self.client.get("/admin/dashboard/")
            self.assertContains(dash, f"signed in as <strong>{role}</strong>")
            team = self.client.get("/admin/team/")
            self.assertEqual(team.status_code, 200 if role == "Super Admin" else 403, email)
            self.assertEqual(self.client.get("/admin/settings/").status_code, 200 if role == "Super Admin" else 403, email)

    def test_login_page_has_no_role_selector_and_a_single_form(self):
        body = self.client.get("/admin/login/").content.decode()
        self.assertNotIn('name="role"', body)
        self.assertNotIn("<select", body)
        self.assertEqual(body.count("<form"), 1)

    def test_admin_cannot_manage_accounts_after_login(self):
        self.login("amit@datasense.com", SPEC["amit@datasense.com"][2])
        self.change(SPEC["amit@datasense.com"][2], NEW_PASSWORD)
        self.assertEqual(self.client.get("/admin/team/").status_code, 403)
        self.assertEqual(self.client.post("/admin/team/", {"email": "x@datasense.com", "role": "super_admin"}).status_code, 403)
        self.assertFalse(User.objects.filter(email="x@datasense.com").exists())

    def test_voluntary_change_later_works_and_changing_is_logged_without_passwords(self):
        from library.models import ActivityLog
        self.login("vineet@datasense.com", SPEC["vineet@datasense.com"][2])
        self.change(SPEC["vineet@datasense.com"][2], NEW_PASSWORD)
        self.assertContains(self.client.get("/admin/change-password/"), "Change password")
        self.assertEqual(self.change(NEW_PASSWORD, "Another-Strong-Passphrase-7!").status_code, 302)
        logs = " ".join(ActivityLog.objects.values_list("description", flat=True))
        for secret in (NEW_PASSWORD, SPEC["vineet@datasense.com"][2], "Another-Strong"):
            self.assertNotIn(secret, logs)
        self.assertTrue(ActivityLog.objects.filter(action="password_change").exists())

    def test_attempts_on_the_change_form_are_throttled(self):
        self.login("muthu@datasense.com", SPEC["muthu@datasense.com"][2])
        for _ in range(5):
            self.change("wrong-old-password", NEW_PASSWORD)
        self.assertContains(self.change(SPEC["muthu@datasense.com"][2], NEW_PASSWORD), "Too many failed attempts")
        self.assertTrue(User.objects.get(email="muthu@datasense.com").must_change_password)

    def test_other_ways_of_setting_a_password_clear_the_flag(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        u = User.objects.get(email="amit@datasense.com")
        link = f"/admin/activate/{urlsafe_base64_encode(force_bytes(u.pk))}/{default_token_generator.make_token(u)}/"
        self.client.post(link, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertFalse(User.objects.get(email="amit@datasense.com").must_change_password)
