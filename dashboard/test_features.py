import re
from io import StringIO

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from accounts.models import User
from library.models import Category, Skill
from library.tests.helpers import PASSWORD, BaseTestCase, make_zip

MD = "# Pivot Helper\n\nSummary: Builds pivot tables.\n\n- **Domain:** Excel / Pivot tables\n\nLevel: Beginner\n\n## Steps\n\nDo it.\n"


class UploadSkillTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.admin)

    def test_add_page_offers_both_options(self):
        body = self.client.get("/admin/skills/new/").content.decode()
        self.assertIn("Option A: Create Skill", body)
        self.assertIn("Upload Skill", body)
        self.assertIn('accept=".md"', body)

    def test_upload_md_prefills_form_without_saving(self):
        before = Skill.objects.count()
        r = self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("pivot.md", MD.encode())})
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Review before saving", body)
        self.assertIn('value="Pivot Helper"', body)
        self.assertIn("Builds pivot tables.", body)
        self.assertIn("## Steps", body)
        self.assertEqual(Skill.objects.count(), before)  # nothing saved yet

    def test_uploaded_skill_saves_as_draft_by_default_and_keeps_filename(self):
        r = self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("pivot.md", MD.encode())})
        form = {"title": "Pivot Helper", "slug": "pivot-helper", "description": "Builds pivot tables.", "category": self.cat.pk, "new_category": "",
                "tags": "Pivot tables, Beginner", "content": MD, "source_filename": "pivot.md", "status": "draft"}
        self.assertRedirects(self.client.post("/admin/skills/new/", form), "/admin/skills/")  
        s = Skill.objects.get(slug="pivot-helper")
        self.assertEqual((s.status, s.original_filename, s.created_by), ("draft", "pivot.md", self.admin))
        self.assertEqual(self.client.get("/skills/pivot-helper/").status_code, 404)  # still private

    def test_suggests_existing_or_new_category_and_flags_clashes(self):
        body = self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("p.md", MD.encode())}).content.decode()
        self.assertIn('<option value="%d" selected>Excel</option>' % self.cat.pk, body)
        md2 = MD.replace("Excel / Pivot", "Brand New Area / Pivot")
        body = self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("p.md", md2.encode())}).content.decode()
        self.assertIn('value="Brand New Area"', body)
        dup = self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("x.md", self.pub.content.encode())}).content.decode()
        self.assertIn("A similar skill already exists", dup)

    def test_new_category_created_on_save(self):
        form = {"title": "Cat Test Skill", "slug": "", "description": "", "category": "", "new_category": "Fresh Area", "tags": "", "content": "# x\n\nbody", "status": "draft"}
        self.client.post("/admin/skills/new/", form)
        self.assertEqual(Skill.objects.get(title="Cat Test Skill").category.name, "Fresh Area")
        bad = self.client.post("/admin/skills/new/", {**form, "title": "No Cat", "new_category": ""})
        self.assertContains(bad, "Choose a category")

    def test_rejects_wrong_type_oversize_and_binary(self):
        for f in (SimpleUploadedFile("evil.exe", b"MZ"), SimpleUploadedFile("notes.txt", b"hi"), SimpleUploadedFile("bin.md", b"a\x00b")):
            r = self.client.post("/admin/skills/upload/", {"file": f}, follow=True)
            self.assertContains(r, "Add Skill")
            self.assertFalse(Skill.objects.filter(original_filename=f.name).exists())
        with override_settings(IMPORT_MAX_ENTRY_BYTES=10):
            r = self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("big.md", MD.encode())}, follow=True)
            self.assertContains(r, "larger than the per-file limit")

    def test_upload_requires_login_and_post(self):
        self.client.logout()
        self.assertEqual(self.client.post("/admin/skills/upload/", {"file": SimpleUploadedFile("p.md", MD.encode())}).status_code, 302)
        self.login(self.admin)
        self.assertEqual(self.client.get("/admin/skills/upload/").status_code, 405)

    def test_markdown_preview_is_sanitised_and_protected(self):
        r = self.client.post("/admin/skills/markdown-preview/", {"content": "# Hi\n\n<script>alert(1)</script> **bold**"})
        html = r.json()["html"]
        self.assertIn("<strong>bold</strong>", html)
        self.assertNotIn("<script", html)
        self.assertEqual(self.client.get("/admin/skills/markdown-preview/").status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post("/admin/skills/markdown-preview/", {"content": "x"}).status_code, 302)


class BulkImportTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.admin)

    def upload(self, *files):
        return self.client.post("/admin/import/", {"files": list(files)})

    def test_workflow_multiple_md_files_default_to_draft(self):
        r = self.upload(SimpleUploadedFile("a.md", b"# Alpha Skill\n\nFirst."), SimpleUploadedFile("b.md", b"# Beta Skill\n\nSecond."))
        review = self.client.get(r["Location"])
        self.assertContains(review, "Skills found")
        self.assertContains(review, '<input type="radio" name="status" value="draft" checked>')
        done = self.client.post(r["Location"], {"status": "draft", "action_0": "import", "action_1": "import", "category_0": "Imports", "category_1": "Imports", "title_0": "Alpha Skill", "title_1": "Beta Skill"})
        self.assertContains(done, "Import results")
        self.assertEqual(set(Skill.objects.filter(category__name="Imports").values_list("status", flat=True)), {"draft"})
        self.assertEqual(self.client.get("/skills/alpha-skill/").status_code, 404)

    def test_no_status_posted_means_draft_even_if_forged_to_something_else(self):
        r = self.upload(SimpleUploadedFile("a.md", b"# Alpha Skill\n\nFirst."))
        self.client.post(r["Location"], {"status": "archived", "action_0": "import", "category_0": "X", "title_0": "Alpha Skill"})
        self.assertEqual(Skill.objects.get(title="Alpha Skill").status, "draft")

    def test_published_only_when_chosen(self):
        r = self.upload(SimpleUploadedFile("a.md", b"# Alpha Skill\n\nFirst."))
        self.client.post(r["Location"], {"status": "published", "action_0": "import", "category_0": "X", "title_0": "Alpha Skill"})
        self.assertEqual(Skill.objects.get(title="Alpha Skill").status, "published")
        self.assertEqual(self.client.get("/skills/alpha-skill/").status_code, 200)

    def test_zip_with_many_skills_and_supporting_files_is_analysed(self):
        z = make_zip({
            "Sales/Pitch Builder.md": "# Pitch Builder\n\nx", "Sales/Cold Email.md": "# Cold Email\n\ny", "Ops/Checklist.md": "# Checklist\n\nz",
            "Ops/Board Pack/SKILL.md": "# Board Pack\n\nUse it.", "Ops/Board Pack/template.xlsx": b"PK", "Ops/Board Pack/tool.exe": b"MZ",
            "../escape.md": "# Escape\n\nno", "readme.pdf": b"%PDF",
        })
        r = self.upload(SimpleUploadedFile("library.zip", z))
        review = self.client.get(r["Location"]).content.decode()
        self.assertIn("Skills found", review)
        self.assertIn("New domain", review)
        self.assertIn("1 supporting file", review)
        self.assertIn("unsafe path", review)  # ../escape.md rejected
        self.assertIn("attachment type not allowed", review)  # tool.exe
        self.assertNotIn("Escape", review.split("Skills found")[1].split("<form")[1] if "<form" in review else "")
        self.client.post(r["Location"], {**{f"action_{i}": "import" for i in range(4)}, **{f"category_{i}": c for i, c in enumerate(["Sales", "Sales", "Ops", "Ops"])}, "status": "draft"})
        self.assertEqual(Skill.objects.filter(title__in=["Pitch Builder", "Cold Email", "Checklist", "Board Pack"]).count(), 4)
        bp = Skill.objects.get(title="Board Pack")
        self.assertEqual(bp.attachments.count(), 1)
        self.assertEqual(self.client.get(f"/admin/skills/{bp.pk}/attachments/{bp.attachments.first().pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/skills/{bp.slug}/files/{bp.attachments.first().pk}/").status_code, 404)  # draft: not public

    def test_conflict_flags_and_choices(self):
        r = self.upload(SimpleUploadedFile("dup.md", b"# Formula Explainer\n\nDifferent text"), SimpleUploadedFile("same.md", self.pub.content.encode()))
        review = self.client.get(r["Location"]).content.decode()
        self.assertIn("Duplicate title", review)
        self.assertIn("Duplicate slug", review)
        self.assertIn("Duplicate content", review)
        self.assertIn('value="replace"', review)
        self.assertIn("Rename (keep both)", review)
        self.client.post(r["Location"], {"action_0": "rename", "action_1": "skip", "category_0": "Excel", "category_1": "Excel", "status": "draft"})
        self.assertEqual(Skill.objects.filter(title="Formula Explainer").count(), 2)
        self.assertEqual(Skill.objects.get(slug="formula-explainer").status, "published")  # original untouched

    def test_replace_keeps_one_record(self):
        r = self.upload(SimpleUploadedFile("dup.md", b"# Formula Explainer\n\nReplacement body"))
        self.client.post(r["Location"], {"action_0": "replace", "category_0": "Excel", "title_0": "Formula Explainer", "status": "draft"})
        self.assertEqual(Skill.objects.filter(title="Formula Explainer").count(), 1)
        self.assertIn("Replacement body", Skill.objects.get(title="Formula Explainer").content)

    def test_invalid_files_reported(self):
        r = self.upload(SimpleUploadedFile("bad.zip", b"not a zip"), SimpleUploadedFile("ok.md", b"# Fine Skill\n\nok"))
        review = self.client.get(r["Location"]).content.decode()
        self.assertIn("not a valid ZIP archive", review)
        self.assertIn("Fine Skill", review)

    def test_zip_limits(self):
        z = make_zip({f"s{i}.md": f"# S{i} Skill\n\nx" for i in range(6)})
        with override_settings(IMPORT_MAX_FILES=3):
            r = self.upload(SimpleUploadedFile("many.zip", z))
            self.assertContains(self.client.get(r["Location"]), "too many files")

    def test_cannot_import_without_permission(self):
        self.client.logout()
        self.assertEqual(self.upload(SimpleUploadedFile("a.md", b"# A\n\nx")).status_code, 302)

    def test_bulk_feature_toggle_drives_landing_page(self):
        self.client.post("/admin/skills/bulk/", {"action": "feature", "ids": [self.pub.pk]})
        self.pub.refresh_from_db()
        self.assertTrue(self.pub.is_featured)
        self.assertIn("Featured Skills", self.client.get("/").content.decode())
        self.client.post("/admin/skills/bulk/", {"action": "unfeature", "ids": [self.pub.pk]})
        self.assertNotIn("Featured Skills", self.client.get("/").content.decode())


class PasswordFlowTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_login_page_is_minimal_with_forgot_password(self):
        body = self.client.get("/admin/login/").content.decode()
        self.assertEqual(sorted(re.findall(r'<input[^>]*name="(\w+)"', body)), ["csrfmiddlewaretoken", "email", "next", "password"])
        self.assertIn("Sign In", body)
        self.assertIn("Forgot Password?", body)
        self.assertNotIn("<select", body)

    def test_reset_by_email_end_to_end(self):
        r = self.client.post("/admin/forgot-password/", {"email": "AKHILESH@datasense.com"})
        self.assertRedirects(r, "/admin/forgot-password/sent/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin.email])
        link = re.search(r"http://testserver(/admin/reset/[^\s]+)", mail.outbox[0].body).group(1)
        confirm = self.client.get(link, follow=True)
        self.assertContains(confirm, "Choose a new password")
        form_url = confirm.redirect_chain[-1][0] if confirm.redirect_chain else link
        weak = self.client.post(form_url, {"new_password1": "short", "new_password2": "short"})
        self.assertEqual(weak.status_code, 200)
        new_pw = "A-Brand-New-Passphrase-77"
        done = self.client.post(form_url, {"new_password1": new_pw, "new_password2": new_pw})
        self.assertRedirects(done, "/admin/reset/done/")
        self.assertTrue(User.objects.get(pk=self.admin.pk).check_password(new_pw))
        self.assertRedirects(self.client.post("/admin/login/", {"email": self.admin.email, "password": new_pw}), "/admin/dashboard/")
        self.client.logout()
        self.assertContains(self.client.get(link, follow=True), "not valid")  # single use
        self.assertEqual(self.client.post("/admin/login/", {"email": self.admin.email, "password": PASSWORD}).status_code, 200)  # old password dead

    def test_same_response_for_unknown_inactive_or_unactivated_accounts_and_nothing_sent(self):
        pending = User.objects.create_user("pending@datasense.com")  # unusable password
        from accounts.roles import set_role
        set_role(pending, "admin")
        gone = self.make_user("gone@datasense.com", "admin")
        gone.is_active = False
        gone.save()
        plain = User.objects.create_user("plain@datasense.com", password=PASSWORD)  # no management role
        for email in ("nobody@datasense.com", pending.email, gone.email, plain.email):
            r = self.client.post("/admin/forgot-password/", {"email": email})
            self.assertRedirects(r, "/admin/forgot-password/sent/")
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_requests_are_throttled(self):
        for _ in range(8):
            self.client.post("/admin/forgot-password/", {"email": self.admin.email})
        self.assertEqual(len(mail.outbox), 5)

    def test_done_page_explains_when_email_is_not_configured(self):
        with override_settings(EMAIL_CONFIGURED=False):
            self.assertContains(self.client.get("/admin/forgot-password/sent/"), "E-mail delivery is not configured")
        with override_settings(EMAIL_CONFIGURED=True):
            self.assertNotContains(self.client.get("/admin/forgot-password/sent/"), "E-mail delivery is not configured")


class AuditCommandTests(BaseTestCase):
    def run_audit(self):
        out = StringIO()
        call_command("audit_accounts", stdout=out)
        return out.getvalue()

    def test_reports_missing_pending_and_ready_accounts_without_secrets(self):
        text = self.run_audit()
        self.assertIn("MISSING", text)
        call_command("seed_team", stdout=StringIO())
        text = self.run_audit()
        self.assertIn("vineet@datasense.com", text)
        self.assertIn("no password yet", text)
        self.assertNotIn("MISSING", text)
        for u in User.objects.all():
            u.set_password(PASSWORD)
            u.save()
        text = self.run_audit()
        self.assertIn("All five accounts are in order.", text)
        for secret in ("pbkdf2", PASSWORD, "md5$"):
            self.assertNotIn(secret, text)

    def test_detects_wrong_role_and_deactivation(self):
        call_command("seed_team", stdout=StringIO())
        u = User.objects.get(email="amit@datasense.com")
        u.is_active = False
        u.save()
        from accounts.roles import set_role
        set_role(User.objects.get(email="muthu@datasense.com"), "admin")
        text = self.run_audit()
        self.assertIn("deactivated", text)
        self.assertIn("expected Super Admin", text)
