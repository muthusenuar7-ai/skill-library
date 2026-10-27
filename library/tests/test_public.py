import io
import zipfile

from django.urls import reverse

from library.markdown_utils import render_markdown
from library.models import Category, Skill

from .helpers import BaseTestCase


class PublicLibraryTests(BaseTestCase):
    def test_home_works_without_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Datacense AI Skill Library")
        self.assertContains(r, "Formula Explainer")

    def test_published_skill_visible(self):
        r = self.client.get(f"/skills/{self.pub.slug}/")
        self.assertContains(r, "Explains formulas")

    def test_draft_and_archived_detail_404(self):
        for s in (self.draft, self.archived):
            self.assertEqual(self.client.get(f"/skills/{s.slug}/").status_code, 404)

    def test_draft_and_archived_hidden_from_lists_and_search(self):
        for url in ("/", "/skills/", "/skills/?q=SECRETDRAFT", "/skills/?q=SECRETARCHIVED", f"/categories/{self.cat.slug}/", "/favorites/"):
            body = self.client.get(url).content.decode()
            self.assertNotIn("Secret Draft", body, url)
            self.assertNotIn("Old Archived", body, url)

    def test_draft_and_archived_downloads_404(self):
        for s in (self.draft, self.archived):
            self.assertEqual(self.client.get(f"/skills/{s.slug}/download/").status_code, 404)
            self.assertEqual(self.client.get(f"/skills/{s.slug}/package/").status_code, 404)

    def test_category_and_all_zip_exclude_unpublished(self):
        for url in (f"/downloads/category/{self.cat.slug}/", "/downloads/all/"):
            r = self.client.get(url)
            self.assertEqual(r["Content-Type"], "application/zip")
            names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
            self.assertEqual(names, ["Formula-Explainer.md"])  # flat, named after the skill title

    def test_category_without_published_skills_download_404(self):
        self.assertEqual(self.client.get(f"/downloads/category/{self.other_cat.slug}/").status_code, 404)

    def test_inactive_category_hides_its_skills(self):
        self.cat.is_active = False
        self.cat.save()
        self.assertEqual(self.client.get(f"/skills/{self.pub.slug}/").status_code, 404)
        self.assertEqual(self.client.get(f"/skills/{self.pub.slug}/download/").status_code, 404)
        self.assertEqual(self.client.get(f"/categories/{self.cat.slug}/").status_code, 404)

    def test_search_and_category_filter(self):
        Skill.objects.create(title="DAX Context", category=self.other_cat, content="dax", status="published", tags="dax")
        self.assertContains(self.client.get("/skills/?q=formulas"), "Formula Explainer")
        self.assertNotContains(self.client.get("/skills/?q=formulas"), "DAX Context")
        body = self.client.get(f"/categories/{self.other_cat.slug}/").content.decode()
        self.assertIn("DAX Context", body)
        self.assertNotIn("Formula Explainer</a></h3>", body)
        self.assertContains(self.client.get("/skills/?q=nomatchatall"), "No skills match")
        self.assertContains(self.client.get("/skills/?tag=dax"), "DAX Context")

    def test_individual_markdown_download(self):
        r = self.client.get(f"/skills/{self.pub.slug}/download/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/markdown", r["Content-Type"])
        self.assertIn('filename="Formula-Explainer.md"', r["Content-Disposition"])
        self.assertIn(b"Explains formulas", r.content)

    def test_admin_pages_require_login(self):
        for url in ("/admin/dashboard/", "/admin/skills/", "/admin/skills/new/", "/admin/import/", "/admin/categories/", "/admin/team/", "/admin/activity/", "/admin/export/", f"/admin/skills/{self.draft.pk}/preview/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/admin/login/", r["Location"])

    def test_markdown_is_sanitised(self):
        html = render_markdown('Hi <script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1)) <a href="https://ok.example" onclick="x()">ok</a>')
        for bad in ("<script", "onerror", "onclick", "javascript:", "<img"):
            self.assertNotIn(bad, html)
        self.assertIn("https://ok.example", html)

    def test_script_in_skill_does_not_reach_page(self):
        s = Skill.objects.create(title="Evil", category=self.cat, content="<script>alert('xss')</script>\n\n**bold**", status="published")
        body = self.client.get(f"/skills/{s.slug}/").content.decode()
        self.assertNotIn("<script>alert", body)
        self.assertIn("<strong>bold</strong>", body)

    def test_security_headers(self):
        r = self.client.get("/")
        self.assertIn("default-src 'self'", r["Content-Security-Policy"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_slug_uniqueness(self):
        a = Skill.objects.create(title="Same Title", category=self.cat, content="a")
        b = Skill.objects.create(title="Same Title", category=self.cat, content="b")
        self.assertNotEqual(a.slug, b.slug)

    def test_unsafe_download_names(self):
        s = Skill.objects.create(title="../../etc/passwd", category=self.cat, content="x", status="published")
        r = self.client.get(f"/skills/{s.slug}/download/")
        self.assertNotIn("..", r["Content-Disposition"])
        self.assertNotIn("/", r["Content-Disposition"].split("filename=")[1])
