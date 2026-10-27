import re
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import override_settings

from accounts.models import User
from library.models import ActivityLog, Category, SiteSetting, Skill
from library.tests.helpers import PASSWORD, BaseTestCase


class AuthTests(BaseTestCase):
    def test_single_login_form_without_role_selector(self):
        body = self.client.get("/admin/login/").content.decode()
        self.assertIn('name="email"', body)
        self.assertIn('name="password"', body)
        self.assertNotIn('name="role"', body)
        self.assertEqual(body.count("<form"), 1)

    def test_super_admin_and_admin_use_same_login_and_land_on_same_dashboard(self):
        for user, label in ((self.super, "Super Admin"), (self.admin, "Admin")):
            r = self.client.post("/admin/login/", {"email": user.email.upper(), "password": PASSWORD})
            self.assertRedirects(r, "/admin/dashboard/")
            body = self.client.get("/admin/dashboard/").content.decode()
            self.assertIn(f"signed in as <strong>{label}</strong>", body)
            self.client.post("/admin/logout/")

    def test_role_decides_navigation(self):
        self.login(self.admin)
        body = self.client.get("/admin/dashboard/").content.decode()
        for visible in ("Manage Skills", "Add Skill", "Import Skills", "Categories", "Activity"):
            self.assertIn(visible, body)
        for hidden in ("Team Management", "/admin/settings/", "Backup &amp; Export", "/admin/team/"):
            self.assertNotIn(hidden, body)
        self.client.logout()
        self.login(self.super)
        body = self.client.get("/admin/dashboard/").content.decode()
        for visible in ("Team Management", "Settings", "Backup &amp; Export"):
            self.assertIn(visible, body)

    def test_logout_is_post_only(self):
        self.login(self.super)
        self.assertEqual(self.client.get("/admin/logout/").status_code, 405)
        self.assertEqual(self.client.post("/admin/logout/").status_code, 302)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 302)

    def test_wrong_password_generic_error_and_lockout(self):
        for _ in range(5):
            r = self.client.post("/admin/login/", {"email": self.admin.email, "password": "wrong-password-1"})
            self.assertContains(r, "Incorrect e-mail address or password")
        r = self.client.post("/admin/login/", {"email": self.admin.email, "password": PASSWORD})
        self.assertEqual(r.status_code, 429)

    def test_unknown_email_same_error(self):
        r = self.client.post("/admin/login/", {"email": "nobody@datasense.com", "password": "whatever-pass-1"})
        self.assertContains(r, "Incorrect e-mail address or password")

    def test_inactive_user_cannot_login_and_is_logged_out(self):
        self.login(self.admin)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 200)
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 302)  # live session stops working
        r = self.client.post("/admin/login/", {"email": self.admin.email, "password": PASSWORD})
        self.assertContains(r, "Incorrect")

    def test_open_redirect_blocked(self):
        r = self.client.post("/admin/login/", {"email": self.admin.email, "password": PASSWORD, "next": "https://evil.example/"})
        self.assertRedirects(r, "/admin/dashboard/")

    def test_email_unique_case_insensitive(self):
        with self.assertRaises(Exception):
            User.objects.create_user("MUTHU@datasense.com", password=PASSWORD)

    def test_user_without_role_cannot_enter(self):
        User.objects.create_user("norole@datasense.com", password=PASSWORD)
        r = self.client.post("/admin/login/", {"email": "norole@datasense.com", "password": PASSWORD})
        self.assertContains(r, "Incorrect")

    def test_super_admin_is_not_django_superuser(self):
        for u in (self.super, self.admin):
            u.refresh_from_db()
            self.assertFalse(u.is_superuser)
            self.assertFalse(u.is_staff)
        self.assertEqual(self.super.role, "super_admin")
        self.assertTrue(self.super.has_perm("accounts.manage_team"))
        self.assertFalse(self.admin.has_perm("accounts.manage_team"))

    def test_django_admin_only_for_framework_superuser(self):
        for user in (self.admin, self.super):
            self.login(user)
            self.assertEqual(self.client.get("/django-admin/").status_code, 302, user.email)
            self.client.logout()
        root = User.objects.create_superuser("root@datasense.com", PASSWORD)
        self.assertIsNone(root.role)
        self.assertTrue(self.client.login(username=root.email, password=PASSWORD))
        self.assertEqual(self.client.get("/django-admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 403)  # framework superuser has no management role


class AccessControlTests(BaseTestCase):
    def test_public_pages_have_no_management_controls(self):
        for url in ("/", "/skills/", f"/skills/{self.pub.slug}/", f"/categories/{self.cat.slug}/", "/favorites/"):
            body = self.client.get(url).content.decode()
            self.assertNotIn("/admin/", body, url)
            self.assertNotIn("sign in", body.lower(), url)

    def test_public_pages_stay_public_even_when_signed_in_as_admin(self):
        self.login(self.super)
        self.assertNotIn("/admin/", self.client.get("/").content.decode())

    def test_regular_admin_blocked_everywhere_super_only(self):
        self.login(self.admin)
        for url in ("/admin/team/", f"/admin/team/{self.admin.pk}/edit/", "/admin/settings/", "/admin/backup/", "/admin/export/", "/admin/export/?format=json"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for url in (f"/admin/team/{self.super.pk}/link/", f"/admin/team/{self.super.pk}/toggle/"):
            self.assertEqual(self.client.post(url).status_code, 403, url)
        self.assertEqual(self.client.post("/admin/team/", {"email": "x@datasense.com", "role": "super_admin"}).status_code, 403)
        self.assertEqual(self.client.post("/admin/settings/", {"intro_text": "hacked"}).status_code, 403)
        self.assertEqual(self.client.get("/django-admin/").status_code, 302)
        self.assertFalse(User.objects.filter(email="x@datasense.com").exists())
        self.assertNotEqual(SiteSetting.load().intro_text, "hacked")

    def test_forged_privilege_fields_are_ignored(self):
        self.login(self.admin)
        data = {"title": "Forged", "slug": "", "description": "d", "category": self.cat.pk, "tags": "", "content": "# Forged\n\nbody",
                "status": "draft", "role": "super_admin", "is_superuser": "on", "is_staff": "on", "groups": "1", "user_permissions": "1"}
        self.client.post("/admin/skills/new/", data)
        self.client.post(f"/admin/skills/{self.pub.pk}/edit/", {**data, "title": "Edited"})
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.is_superuser, self.admin.is_staff, self.admin.role), (False, False, "admin"))
        self.assertFalse(self.admin.has_perm("accounts.manage_team"))
        # an Admin cannot even reach the endpoint that changes roles, with any body
        r = self.client.post(f"/admin/team/{self.admin.pk}/edit/", {"full_name": "x", "email": self.admin.email, "role": "super_admin"})
        self.assertEqual(r.status_code, 403)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, "admin")

    def test_admin_cannot_edit_existing_categories(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(f"/admin/categories/{self.cat.pk}/edit/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/categories/{self.cat.pk}/edit/", {"name": "Renamed", "display_order": 1}).status_code, 403)
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.name, "Excel")

    def test_activity_scope_by_role(self):
        ActivityLog.objects.create(actor=self.super, actor_email=self.super.email, action="user_create", target_type="user", description="Created someone SECRETTEAMEVENT")
        ActivityLog.objects.create(actor=self.super, actor_email=self.super.email, action="skill_create", target_type="skill", description="Created a skill VISIBLEEVENT")
        self.login(self.admin)
        body = self.client.get("/admin/activity/").content.decode()
        self.assertIn("VISIBLEEVENT", body)
        self.assertNotIn("SECRETTEAMEVENT", body)
        self.client.logout()
        self.login(self.super)
        body = self.client.get("/admin/activity/").content.decode()
        self.assertIn("SECRETTEAMEVENT", body)

    def test_anonymous_cannot_reach_any_management_url(self):
        for url in ("/admin/dashboard/", "/admin/skills/", "/admin/skills/new/", "/admin/import/", "/admin/categories/", "/admin/team/",
                    "/admin/settings/", "/admin/backup/", "/admin/activity/", "/admin/export/", f"/admin/skills/{self.draft.pk}/preview/", f"/admin/skills/{self.draft.pk}/download/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/admin/login/", r["Location"])
        for url in ("/admin/skills/bulk/", f"/admin/skills/{self.draft.pk}/status/", f"/admin/team/{self.admin.pk}/toggle/"):
            self.assertEqual(self.client.post(url, {"action": "publish"}).status_code, 302, url)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "draft")


class TeamTests(BaseTestCase):
    def create(self, **kw):
        data = {"email": "New.Person@Datasense.com", "full_name": "New Person", "role": "admin"}
        data.update(kw)
        return self.client.post("/admin/team/", data)

    def test_create_admin_shows_secure_link_when_email_not_configured(self):
        self.login(self.super)
        r = self.create()
        self.assertContains(r, "E-mail delivery is not configured")
        link = re.search(r"http://testserver(/admin/activate/[^<\s]+)", r.content.decode()).group(1)
        user = User.objects.get(email="new.person@datasense.com")
        self.assertFalse(user.has_usable_password())
        self.assertEqual((user.role, user.is_superuser), ("admin", False))
        self.client.logout()
        self.assertEqual(self.client.post(link, {"new_password1": "short", "new_password2": "short"}).status_code, 200)
        self.assertRedirects(self.client.post(link, {"new_password1": PASSWORD, "new_password2": PASSWORD}), "/admin/login/")
        self.assertEqual(self.client.get(link).status_code, 400)  # single use
        self.assertRedirects(self.client.post("/admin/login/", {"email": "new.person@datasense.com", "password": PASSWORD}), "/admin/dashboard/")

    @override_settings(EMAIL_CONFIGURED=True, SITE_URL="https://skills.example.com")
    def test_invitation_email_sent_when_configured(self):
        self.login(self.super)
        r = self.create(email="mailed@datasense.com")
        self.assertContains(r, "An e-mail with a one-time link was sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mailed@datasense.com"])
        self.assertIn("https://skills.example.com/admin/activate/", mail.outbox[0].body)
        self.assertNotIn("/admin/activate/", r.content.decode())  # link is not shown on screen when e-mailed

    def test_super_admin_can_create_super_admin(self):
        self.login(self.super)
        self.create(email="second.super@datasense.com", role="super_admin")
        u = User.objects.get(email="second.super@datasense.com")
        self.assertEqual((u.role, u.is_superuser, u.is_staff), ("super_admin", False, False))

    def test_duplicate_email_rejected_case_insensitively(self):
        self.login(self.super)
        self.assertContains(self.create(email="AKHILESH@datasense.com"), "already exists")

    def test_edit_details_and_change_role(self):
        self.login(self.super)
        r = self.client.post(f"/admin/team/{self.admin.pk}/edit/", {"full_name": "Akhilesh K", "email": "akhilesh.k@datasense.com", "role": "super_admin"})
        self.assertRedirects(r, "/admin/team/")
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.full_name, self.admin.email, self.admin.role), ("Akhilesh K", "akhilesh.k@datasense.com", "super_admin"))
        self.assertTrue(ActivityLog.objects.filter(action="user_role_change").exists())
        self.client.post(f"/admin/team/{self.admin.pk}/edit/", {"full_name": "Akhilesh K", "email": "akhilesh.k@datasense.com", "role": "admin"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, "admin")

    def test_edit_rejects_email_used_by_someone_else(self):
        self.login(self.super)
        r = self.client.post(f"/admin/team/{self.admin.pk}/edit/", {"full_name": "A", "email": self.super.email, "role": "admin"})
        self.assertContains(r, "Another account")

    def test_deactivate_and_reactivate(self):
        self.login(self.super)
        self.client.post(f"/admin/team/{self.admin.pk}/toggle/")
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)
        self.client.logout()
        self.assertContains(self.client.post("/admin/login/", {"email": self.admin.email, "password": PASSWORD}), "Incorrect")
        self.login(self.super)
        self.client.post(f"/admin/team/{self.admin.pk}/toggle/")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertEqual(list(ActivityLog.objects.filter(action__in=["user_deactivate", "user_reactivate"]).values_list("action", flat=True).order_by("id")), ["user_deactivate", "user_reactivate"])

    def test_cannot_deactivate_self_or_last_super_admin(self):
        self.login(self.super)
        self.client.post(f"/admin/team/{self.super.pk}/toggle/")
        self.super.refresh_from_db()
        self.assertTrue(self.super.is_active)
        # a second super admin tries to deactivate the only other one -> allowed; then the last one is protected
        other = self.make_user("vineet@datasense.com", "super_admin")
        self.client.logout()
        self.login(other)
        self.client.post(f"/admin/team/{self.super.pk}/toggle/")
        self.super.refresh_from_db()
        self.assertFalse(self.super.is_active)
        self.client.post(f"/admin/team/{other.pk}/toggle/")
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_cannot_change_own_role_or_demote_last_super(self):
        self.login(self.super)
        r = self.client.post(f"/admin/team/{self.super.pk}/edit/", {"full_name": "", "email": self.super.email, "role": "admin"})
        self.assertContains(r, "cannot change your own role")
        other = self.make_user("vineet@datasense.com", "super_admin")
        self.client.logout()
        self.login(other)
        self.client.post(f"/admin/team/{self.super.pk}/edit/", {"full_name": "", "email": self.super.email, "role": "admin"})
        self.super.refresh_from_db()
        self.assertEqual(self.super.role, "admin")  # allowed: another active Super Admin (the actor) remains
        mine = self.client.post(f"/admin/team/{other.pk}/edit/", {"full_name": "", "email": other.email, "role": "admin"})
        self.assertContains(mine, "cannot change your own role")
        other.refresh_from_db()
        self.assertEqual(other.role, "super_admin")

    def test_reset_password_issues_new_link(self):
        self.login(self.super)
        r = self.client.post(f"/admin/team/{self.admin.pk}/link/")
        self.assertContains(r, "Password reset for akhilesh@datasense.com")
        link = re.search(r"http://testserver(/admin/activate/[^<\s]+)", r.content.decode()).group(1)
        self.client.logout()
        new_pw = "Another-Long-Password-42"
        self.client.post(link, {"new_password1": new_pw, "new_password2": new_pw})
        self.assertTrue(User.objects.get(pk=self.admin.pk).check_password(new_pw))

    def test_passwords_never_shown(self):
        self.login(self.super)
        body = self.client.get("/admin/team/").content.decode()
        self.assertNotIn("pbkdf2", body)
        self.assertNotIn(PASSWORD, body)

    def test_seed_team_creates_accounts_without_passwords(self):
        out = StringIO()
        call_command("seed_team", stdout=out)
        for email, role in (("muthu@datasense.com", "super_admin"), ("vineet@datasense.com", "super_admin"), ("akhilesh@datasense.com", "admin"),
                            ("himanshi@datasense.com", "admin"), ("amit@datasense.com", "admin")):
            u = User.objects.get(email=email)
            self.assertEqual((u.role, u.is_superuser, u.is_staff), (role, False, False), email)
        for email in ("vineet@datasense.com", "himanshi@datasense.com", "amit@datasense.com"):
            self.assertFalse(User.objects.get(email=email).has_usable_password())
        self.assertTrue(User.objects.get(email=self.super.email).has_usable_password())  # untouched
        self.assertIn("/admin/activate/", out.getvalue())
        self.assertNotIn(PASSWORD, out.getvalue())
        call_command("seed_team", stdout=StringIO())
        self.assertEqual(User.objects.count(), 5)  # repeatable


class SkillManagementTests(BaseTestCase):
    def data(self, **kw):
        d = {"title": "Meeting Notes Helper", "slug": "", "description": "Turns notes into minutes", "category": self.cat.pk,
             "tags": "meetings, office", "content": "# Meeting Notes Helper\n\nBody", "status": "draft"}
        d.update(kw)
        return d

    def test_admin_adds_and_edits_skill(self):
        self.login(self.admin)
        self.assertRedirects(self.client.post("/admin/skills/new/", self.data()), "/admin/skills/")
        s = Skill.objects.get(title="Meeting Notes Helper")
        self.assertEqual((s.slug, s.created_by, s.status), ("meeting-notes-helper", self.admin, "draft"))
        self.client.post(f"/admin/skills/{s.pk}/edit/", self.data(title="Meeting Notes Helper v2", slug="meeting-notes-helper", status="published"))
        s.refresh_from_db()
        self.assertEqual((s.title, s.status), ("Meeting Notes Helper v2", "published"))
        self.assertContains(self.client.logout() or self.client.get("/skills/meeting-notes-helper/"), "Meeting Notes Helper v2")  # public sees the change at once

    def test_validation(self):
        self.login(self.admin)
        self.assertContains(self.client.post("/admin/skills/new/", self.data(title="ab")), "at least 3")
        self.assertContains(self.client.post("/admin/skills/new/", self.data(content="  ")), "Add Markdown content")
        self.assertContains(self.client.post("/admin/skills/new/", self.data(slug="Bad Slug!")), "slug")
        self.assertContains(self.client.post("/admin/skills/new/", self.data(slug=self.pub.slug)), "already uses")
        self.assertContains(self.client.post("/admin/skills/new/", self.data(tags=",".join("t%d" % i for i in range(12)))), "at most 10")

    def test_publish_unpublish_archive_update_public_library(self):
        self.login(self.admin)
        url = f"/admin/skills/{self.draft.pk}/status/"
        self.assertEqual(self.client.get(f"/skills/{self.draft.slug}/").status_code, 404)
        self.client.post(url, {"action": "publish"})
        self.assertEqual(self.client.get(f"/skills/{self.draft.slug}/").status_code, 200)
        self.assertContains(self.client.get("/skills/"), "Secret Draft")
        self.client.post(url, {"action": "unpublish"})
        self.assertEqual(self.client.get(f"/skills/{self.draft.slug}/").status_code, 404)
        self.client.post(f"/admin/skills/{self.pub.pk}/status/", {"action": "archive"})
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.status, "archived")
        self.assertEqual(self.client.get(f"/skills/{self.pub.slug}/").status_code, 404)

    def test_bulk_actions(self):
        self.login(self.admin)
        a = Skill.objects.create(title="Bulk A", category=self.cat, content="a")
        b = Skill.objects.create(title="Bulk B", category=self.cat, content="b")
        self.client.post("/admin/skills/bulk/", {"action": "publish", "ids": [a.pk, b.pk]})
        self.assertEqual(Skill.objects.filter(pk__in=[a.pk, b.pk], status="published").count(), 2)
        self.client.post("/admin/skills/bulk/", {"action": "archive", "ids": [a.pk]})
        a.refresh_from_db()
        self.assertEqual(a.status, "archived")
        self.assertEqual(self.client.get(f"/skills/{b.slug}/").status_code, 200)
        r = self.client.post("/admin/skills/bulk/", {"action": "delete-everything", "ids": [b.pk]}, follow=True)
        self.assertEqual(Skill.objects.filter(pk=b.pk, status="published").count(), 1)
        self.assertTrue(ActivityLog.objects.filter(action="skill_bulk_publish").exists())

    def test_status_requires_post_and_csrf(self):
        from django.test import Client
        self.login(self.admin)
        self.assertEqual(self.client.get(f"/admin/skills/{self.draft.pk}/status/").status_code, 405)
        c = Client(enforce_csrf_checks=True)
        c.login(username=self.admin.email, password=PASSWORD)
        self.assertEqual(c.post(f"/admin/skills/{self.draft.pk}/status/", {"action": "publish"}).status_code, 403)
        self.assertEqual(c.post("/admin/skills/bulk/", {"action": "publish", "ids": [self.draft.pk]}).status_code, 403)

    def test_preview_and_download_of_draft_for_management_only(self):
        self.assertEqual(self.client.get(f"/admin/skills/{self.draft.pk}/download/").status_code, 302)
        self.login(self.admin)
        self.assertContains(self.client.get(f"/admin/skills/{self.draft.pk}/preview/"), "Management preview")
        self.assertIn(b"SECRETDRAFT", self.client.get(f"/admin/skills/{self.draft.pk}/download/").content)

    def test_category_management_super_admin(self):
        self.login(self.super)
        self.assertRedirects(self.client.post("/admin/categories/", {"name": "Training", "slug": "", "description": "", "display_order": 3, "is_active": "on"}), "/admin/categories/")
        cat = Category.objects.get(name="Training")
        self.assertEqual(cat.slug, "training")
        self.assertContains(self.client.post("/admin/categories/", {"name": "training", "display_order": 1}), "already exists")
        self.client.post(f"/admin/categories/{cat.pk}/edit/", {"name": "Training", "slug": "training", "display_order": 3})
        cat.refresh_from_db()
        self.assertFalse(cat.is_active)

    def test_admin_can_add_but_not_edit_category(self):
        self.login(self.admin)
        self.assertRedirects(self.client.post("/admin/categories/", {"name": "Fresh", "display_order": 1, "is_active": "on"}), "/admin/categories/")
        self.assertNotContains(self.client.get("/admin/categories/"), "/edit/")

    def test_settings_toggle_controls_library_download(self):
        self.assertEqual(self.client.get("/downloads/all/").status_code, 200)
        self.assertContains(self.client.get("/"), "/downloads/all/")
        self.login(self.super)
        r = self.client.post("/admin/settings/", {"intro_text": "Custom intro line", "footer_note": ""})  # checkbox unticked
        self.assertRedirects(r, "/admin/settings/")
        self.client.logout()
        self.assertEqual(self.client.get("/downloads/all/").status_code, 404)
        home = self.client.get("/")
        self.assertNotContains(home, "/downloads/all/")
        self.assertContains(self.client.get("/about/"), "Custom intro line")
        self.assertEqual(self.client.get(f"/downloads/category/{self.cat.slug}/").status_code, 200)  # categories unaffected
        self.assertEqual(self.client.get("/downloads/selection/").status_code, 404)  # unfiltered selection == whole library
        self.assertEqual(self.client.get("/downloads/selection/?q=formula").status_code, 200)

    def test_backup_super_admin_only_and_contains_drafts(self):
        self.login(self.super)
        self.assertContains(self.client.get("/admin/backup/"), "Download full backup")
        self.assertIn(b"SECRETDRAFT", self.client.get("/admin/export/?format=json").content)
        self.assertEqual(self.client.get("/admin/export/")["Content-Type"], "application/zip")

    def test_persistence_across_sessions(self):
        self.login(self.admin)
        self.client.post("/admin/skills/new/", self.data(status="published"))
        self.client.logout()
        from django.test import Client
        fresh = Client()
        self.assertContains(fresh.get("/skills/meeting-notes-helper/"), "Meeting Notes Helper")


class DeploymentSettingsTests(BaseTestCase):
    def _run(self, *args, **env_over):
        import os
        import subprocess
        import sys
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SECRET_KEY"}
        env.update({"DJANGO_ENV": "production", "DJANGO_DEBUG": "False", "PRIVATE_MEDIA_ROOT": str(base / "private_media")})
        env.update(env_over)
        return subprocess.run([sys.executable, "manage.py", *args], cwd=base, env=env, capture_output=True, text=True)

    def test_production_checks_pass(self):
        res = self._run("check", "--deploy", "--fail-level", "WARNING", DJANGO_SECRET_KEY="x7#kP9!mQ2$vL5@nR8&wT3^yU6*zA1+bC4-dE0_fG" * 2, DJANGO_ALLOWED_HOSTS="skills.example.com")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("no issues", res.stdout)

    def test_production_refuses_missing_secret_key(self):
        res = self._run("check")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", res.stderr)
