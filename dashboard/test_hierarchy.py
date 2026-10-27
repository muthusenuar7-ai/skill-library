import json

from django.core.files.uploadedfile import SimpleUploadedFile

from library import exports, importer
from library.models import Category, Skill
from library.tests.helpers import BaseTestCase, make_zip


class HierarchyAdminTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.da = Category.objects.create(name="Data Analytics")
        self.pbi = Category.objects.create(name="Power BI", parent=self.da)
        self.biz = Category.objects.create(name="Business")
        self.fin = Category.objects.create(name="Finance", parent=self.biz)
        self.login(self.admin)

    def data(self, **kw):
        d = {"title": "DAX Helper", "slug": "", "description": "Helps with DAX", "category": self.da.pk, "new_category": "", "subcategory": self.pbi.pk,
             "new_subcategory": "", "tags": "dax", "content": "# DAX Helper\n\nBody", "status": "draft"}
        d.update(kw)
        return d

    def test_journey_admin_add_assign_draft_publish_verify_public(self):
        self.assertRedirects(self.client.post("/admin/skills/new/", self.data()), "/admin/skills/")
        s = Skill.objects.get(title="DAX Helper")
        self.assertEqual((s.category, s.subcategory, s.status), (self.da, self.pbi, "draft"))
        self.assertNotIn("DAX Helper", self.client.get(f"/skills/?sub={self.pbi.slug}").content.decode())  # draft: private
        self.client.post(f"/admin/skills/{s.pk}/status/", {"action": "publish"})
        self.assertIn("DAX Helper", self.client.get(f"/skills/?domain={self.da.slug}&sub={self.pbi.slug}").content.decode())
        self.assertIn("Power BI", self.client.get(f"/skills/{s.slug}/").content.decode())

    def test_subcategory_must_belong_to_selected_domain(self):
        r = self.client.post("/admin/skills/new/", self.data(category=self.biz.pk))  # Power BI is not under Business
        self.assertContains(r, "belongs to a different major domain")
        self.assertFalse(Skill.objects.filter(title="DAX Helper").exists())

    def test_form_offers_domain_dependent_subcategories(self):
        body = self.client.get("/admin/skills/new/").content.decode()
        self.assertIn(f'data-parent="{self.da.pk}"', body)
        self.assertIn(f'data-parent="{self.biz.pk}"', body)
        self.assertIn("Major domain", body)
        domain_select = body.split('name="category"')[1].split("</select>")[0]
        self.assertIn("Data Analytics", domain_select)
        self.assertNotIn(f'<option value="{self.pbi.pk}"', domain_select)  # only top-level categories are listed as domains

    def test_new_subcategory_created_under_chosen_domain(self):
        self.client.post("/admin/skills/new/", self.data(subcategory="", new_subcategory="SQL"))
        s = Skill.objects.get(title="DAX Helper")
        self.assertEqual((s.subcategory.name, s.subcategory.parent), ("SQL", self.da))

    def test_edit_keeps_and_changes_assignment(self):
        self.client.post("/admin/skills/new/", self.data())
        s = Skill.objects.get(title="DAX Helper")
        self.client.post(f"/admin/skills/{s.pk}/edit/", self.data(category=self.biz.pk, subcategory=self.fin.pk))
        s.refresh_from_db()
        self.assertEqual((s.category, s.subcategory), (self.biz, self.fin))
        self.client.post(f"/admin/skills/{s.pk}/edit/", self.data(category=self.biz.pk, subcategory=""))
        s.refresh_from_db()
        self.assertIsNone(s.subcategory)

    def test_manage_skills_shows_domain_and_subcategory(self):
        self.client.post("/admin/skills/new/", self.data())
        body = self.client.get("/admin/skills/").content.decode()
        self.assertIn('<span class="sub">Power BI</span>', body)
        self.assertIn("DAX Helper", self.client.get("/admin/skills/?q=power+bi").content.decode())

    def test_journey_bulk_import_zip_review_classification_and_verify(self):
        z = make_zip({"Data Analytics/Power BI/DAX Helper.md": "# DAX Helper\n\nx", "Data Analytics/SQL/Joins.md": "# Joins Helper\n\ny", "Business/Budget Notes.md": "# Budget Notes\n\nz"})
        r = self.client.post("/admin/import/", {"files": [SimpleUploadedFile("lib.zip", z)]})
        review = self.client.get(r["Location"]).content.decode()
        self.assertIn('value="Data Analytics"', review)
        self.assertIn('value="Power BI"', review)
        self.assertIn("Existing subcategory", review)  # Power BI exists under Data Analytics
        self.assertIn("New subcategory", review)  # SQL does not
        done = self.client.post(r["Location"], {
            "status": "published", "action_0": "import", "action_1": "import", "action_2": "import",
            "category_0": "Data Analytics", "subcategory_0": "Power BI", "category_1": "Data Analytics", "subcategory_1": "SQL Server",
            "category_2": "Business", "subcategory_2": "", "title_0": "DAX Helper", "title_1": "Joins Helper", "title_2": "Budget Notes"})
        self.assertContains(done, "Import results")
        self.assertEqual(Skill.objects.get(title="DAX Helper").subcategory, self.pbi)
        joins = Skill.objects.get(title="Joins Helper")
        self.assertEqual((joins.category, joins.subcategory.name), (self.da, "SQL Server"))  # the administrator's correction was applied
        self.assertIsNone(Skill.objects.get(title="Budget Notes").subcategory)
        self.assertEqual(Category.objects.filter(parent=self.da).count(), 2)  # no duplicate Power BI
        self.assertIn("Joins Helper", self.client.get(f"/skills/?sub={joins.subcategory.slug}").content.decode())

    def test_administrator_backup_zip_roundtrip_rebuilds_hierarchy(self):
        # the public ZIPs are flat; the administrator backup keeps the full library.json and restores the hierarchy
        self.client.post("/admin/skills/new/", self.data(status="published"))
        data = exports.backup_zip_response().content
        Skill.objects.all().delete()
        cands, _ = importer.analyse_uploads([("export.zip", data)])
        self.assertEqual((cands[0]["category"], cands[0]["subcategory"]), ("Data Analytics", "Power BI"))
        importer.commit_candidates(self.admin, cands, status="published")
        s = Skill.objects.get(title="DAX Helper")
        self.assertEqual((s.category, s.subcategory), (self.da, self.pbi))

    def test_json_backup_roundtrip_keeps_subcategory(self):
        self.client.post("/admin/skills/new/", self.data())
        payload = json.dumps(exports.library_dict()).encode()
        self.assertIn(b'"subcategory": "Power BI"', payload)
        Skill.objects.all().delete()
        cands, _ = importer.analyse_uploads([("lib.json", payload)])
        importer.commit_candidates(self.admin, cands, status="draft")
        self.assertEqual(Skill.objects.get(title="DAX Helper").subcategory, self.pbi)

    def test_category_management_hierarchy_and_permissions(self):
        self.client.logout()
        self.login(self.super)
        r = self.client.post("/admin/categories/", {"name": "Python Analytics", "parent": self.da.pk, "slug": "", "display_order": 1, "is_active": "on"})
        self.assertRedirects(r, "/admin/categories/")
        sub = Category.objects.get(name="Python Analytics")
        self.assertEqual(sub.parent, self.da)
        self.assertIn("Python Analytics", self.client.get("/admin/categories/").content.decode())
        deep = self.client.post("/admin/categories/", {"name": "Too Deep", "parent": sub.pk, "display_order": 1})
        self.assertEqual(deep.status_code, 200)  # parent choices only list major domains
        self.assertFalse(Category.objects.filter(name="Too Deep").exists())
        self.assertContains(self.client.post("/admin/categories/", {"name": "power bi", "parent": self.da.pk, "display_order": 1}), "already exists")
        Skill.objects.create(title="Busy", category=self.biz, content="x")
        r = self.client.post(f"/admin/categories/{self.biz.pk}/edit/", {"name": "Business", "parent": self.da.pk, "slug": "business", "display_order": 1, "is_active": "on"})
        self.assertEqual(r.status_code, 200)  # a domain that already holds skills cannot become a subcategory
        self.biz.refresh_from_db()
        self.assertIsNone(self.biz.parent)

    def test_admin_still_cannot_edit_categories(self):
        self.assertEqual(self.client.get(f"/admin/categories/{self.da.pk}/edit/").status_code, 403)
