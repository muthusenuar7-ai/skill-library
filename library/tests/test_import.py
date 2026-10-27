from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from library import importer
from library.models import Category, Skill

from .helpers import BaseTestCase, make_zip

GUIDE = "# XL-09 - Pivot Helper\n\n*Datacense Project Skill Guide*\n\n- **Domain:** Excel / Pivot tables\n- **Version:** 0.1.0\n\nSummary: Builds pivot tables.\n\nLevel: Beginner\n\n## 1. Purpose\n\nBody.\n"


class ImportEngineTests(BaseTestCase):
    def analyse(self, *uploads):
        return importer.analyse_uploads(list(uploads))

    def test_single_markdown(self):
        cands, report = self.analyse(("pivot.md", GUIDE.encode()))
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual((c["title"], c["source_ref"], c["category"], c["description"]), ("Pivot Helper", "XL-09", "Excel", "Builds pivot tables."))
        self.assertIn("Pivot tables", c["tags"])
        self.assertEqual(c["action"], "import")
        self.assertIn("## 1. Purpose", c["content"])  # original text preserved

    def test_multiple_markdown_and_commit(self):
        cands, _ = self.analyse(("a.md", b"# Alpha Skill\n\nFirst."), ("b.md", b"# Beta Skill\n\nSecond."))
        result = importer.commit_candidates(self.super, cands, status="draft")
        self.assertEqual(result.imported, 2)
        self.assertTrue(Skill.objects.filter(title="Alpha Skill", status="draft", created_by=self.super).exists())
        self.assertIn("Uncategorized", result.categories_created)

    def test_zip_with_folders_infers_categories(self):
        z = make_zip({"Sales/Pitch Builder.md": "# Pitch Builder\n\nx", "Ops/Checklist.md": "# Checklist\n\ny", "__MACOSX/Sales/._Pitch Builder.md": "junk", ".DS_Store": "junk"})
        cands, report = self.analyse(("lib.zip", z))
        self.assertEqual({c["category"] for c in cands}, {"Sales", "Ops"})
        self.assertEqual(len(report.ignored), 2)

    def test_pack_layout_txt_guides_and_docs_ignored(self):
        txt = "DATACENSE PROJECT SKILL GUIDE\n====\nID: XL-02\nTitle: Formula Debugger\nDomain: Excel\nSubdomain: Troubleshooting\n\nSummary: Fixes formulas.\n\nLevel: Beginner\n\nBody"
        z = make_zip({"START_HERE.txt": "hi", "SKILL_INDEX.txt": "idx", "guides/XL-02-formula-debugger.txt": txt, "MANIFEST.json": '{"skills":[{"title":"x","file":"f"}]}'})
        cands, report = self.analyse(("pack.zip", z))
        self.assertEqual([c["title"] for c in cands], ["Formula Debugger"])
        self.assertEqual(cands[0]["category"], "Excel")
        self.assertEqual(cands[0]["source_ref"], "XL-02")
        self.assertEqual(len(report.ignored), 3)

    def test_unsafe_archive_entries_rejected_but_safe_kept(self):
        z = make_zip({
            "../evil.md": "# Evil\n\nx", "/abs/evil2.md": "# Evil2\n\nx", "C:/windows/evil3.md": "# Evil3\n\nx",
            "a/../../evil4.md": "# Evil4\n\nx", "ok/Good Skill.md": "# Good Skill\n\nfine", "back\\..\\evil5.md": "# Evil5\n\nx",
        })
        cands, report = self.analyse(("bad.zip", z))
        self.assertEqual([c["title"] for c in cands], ["Good Skill"])
        self.assertEqual(len(report.rejected), 5)
        self.assertTrue(all("unsafe path" in r for _, r in report.rejected))

    def test_symlink_and_encrypted_flag_rejected(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("link.md")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
            zf.writestr("fine.md", "# Fine One\n\nx")
        cands, report = self.analyse(("l.zip", buf.getvalue()))
        self.assertEqual([c["title"] for c in cands], ["Fine One"])
        self.assertTrue(any("symbolic" in r for _, r in report.rejected))

    def test_limits_enforced(self):
        z = make_zip({f"s{i}.md": f"# Skill {i}\n\nx" for i in range(5)})
        with override_settings(IMPORT_MAX_FILES=3):
            cands, report = self.analyse(("many.zip", z))
        self.assertEqual(cands, [])
        self.assertTrue(report.fatal)
        with override_settings(IMPORT_MAX_UNCOMPRESSED_BYTES=20):
            cands, report = self.analyse(("big.zip", z))
        self.assertTrue(report.fatal)
        with override_settings(IMPORT_MAX_ENTRY_BYTES=10):
            cands, report = self.analyse(("big.zip", z))
        self.assertEqual(cands, [])
        self.assertEqual(len(report.rejected), 5)

    def test_not_a_zip_and_bad_type(self):
        _, report = self.analyse(("fake.zip", b"not a zip"), ("run.exe", b"MZ"))
        self.assertEqual(len(report.fatal), 2)

    def test_package_with_attachments(self):
        z = make_zip({"Reports/Board Pack/SKILL.md": "# Board Pack\n\nUse the template.", "Reports/Board Pack/template.xlsx": b"PK-binary", "Reports/Board Pack/run.exe": b"MZ"})
        cands, report = self.analyse(("pkg.zip", z))
        self.assertEqual(len(cands), 1)
        self.assertEqual([a["path"] for a in cands[0]["attachments"]], ["template.xlsx"])
        self.assertEqual(cands[0]["category"], "Reports")
        self.assertTrue(any("not allowed" in r for _, r in report.rejected))
        importer.commit_candidates(self.super, cands, status="published")
        skill = Skill.objects.get(title="Board Pack")
        self.assertEqual(skill.attachments.count(), 1)
        r = self.client.get(f"/skills/{skill.slug}/package/")
        import io
        import zipfile
        self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()), ["Board-Pack/SKILL.md", "Board-Pack/template.xlsx"])  # the complete package keeps its own structure

    def test_json_library_import(self):
        import json
        data = {"format": "datacense-skill-library", "skills": [{"title": "From JSON", "content": "# From JSON\n\nbody", "category": "Excel", "tags": ["a", "b"]}, {"title": "No Content"}]}
        cands, _ = self.analyse(("lib.json", json.dumps(data).encode()))
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[1]["action"], "error")
        result = importer.commit_candidates(self.super, cands, status="draft")
        self.assertEqual((result.imported, len(result.errors)), (1, 1))

    def test_duplicates_default_to_skip_and_choices_work(self):
        # existing "Formula Explainer" (slug/title) and identical content
        cands, _ = self.analyse(("x.md", b"# Formula Explainer\n\nDifferent text"))
        self.assertEqual(cands[0]["dup"]["kind"], "same slug")
        self.assertEqual(cands[0]["action"], "skip")
        r = importer.commit_candidates(self.super, cands, status="draft")
        self.assertEqual((r.imported, len(r.skipped)), (0, 1))
        self.assertEqual(Skill.objects.filter(title="Formula Explainer").count(), 1)

        r = importer.commit_candidates(self.super, cands, {"0": {"action": "rename"}}, status="draft")
        self.assertEqual(r.imported, 1)
        self.assertEqual(Skill.objects.filter(title="Formula Explainer").count(), 2)

        cands, _ = self.analyse(("x.md", b"# Formula Explainer\n\nReplacement text"))
        original = Skill.objects.get(slug="formula-explainer")
        r = importer.commit_candidates(self.super, cands, {"0": {"action": "replace"}}, status="draft")
        self.assertEqual(len(r.replaced), 1)
        original.refresh_from_db()
        self.assertIn("Replacement text", original.content)
        self.assertEqual(original.status, "published")  # replace keeps publication state

    def test_identical_content_detected(self):
        cands, _ = self.analyse(("copy.md", self.pub.content.encode()))
        self.assertEqual(cands[0]["dup"]["kind"], "identical content")

    def test_duplicate_within_upload(self):
        cands, _ = self.analyse(("a.md", b"# Twin\n\nsame"), ("b.md", b"# Twin\n\nsame"))
        self.assertIsNone(cands[0]["dup"])
        self.assertEqual(cands[1]["dup"]["kind"], "duplicate within this upload")
        r = importer.commit_candidates(self.super, cands, status="draft")
        self.assertEqual(r.imported, 1)

    def test_one_failure_does_not_discard_others(self):
        cands, _ = self.analyse(("a.md", b"# Good One\n\nx"), ("b.md", b"# Good Two\n\ny"))
        cands[0]["action"] = "replace"  # replace without an existing target -> error
        r = importer.commit_candidates(self.super, cands, status="draft")
        self.assertEqual((r.imported, len(r.errors)), (1, 1))
        self.assertTrue(Skill.objects.filter(title="Good Two").exists())
        self.assertFalse(Skill.objects.filter(title="Good One").exists())

    def test_import_library_command_migrates_the_real_sources_once(self):
        from io import StringIO
        Skill.objects.all().delete()
        Category.objects.all().delete()
        out = StringIO()
        call_command("import_library", stdout=out)
        self.assertIn("Source records found ......... 72", out.getvalue())
        self.assertEqual(Skill.objects.count(), 72)  # 62 from the standalone library page + 10 written guides
        self.assertEqual(Skill.objects.filter(status="published").count(), 72)
        self.assertEqual(Category.objects.filter(parent__isnull=True).count(), 25)
        self.assertEqual(Category.objects.filter(parent__isnull=False).count(), 65)
        self.assertEqual(Skill.objects.filter(subcategory__isnull=True).count(), 0)
        self.assertEqual(Skill.objects.filter(source_ref__startswith="base-").count(), 25)
        self.assertEqual(Skill.objects.filter(source_ref__startswith="collection-").count(), 37)
        self.assertEqual(Skill.objects.filter(slug__endswith="-expanded").count(), 5)  # same-name skills with different instructions are both kept
        self.assertEqual(Skill.objects.values("title").distinct().count(), 72)  # no two skills share a title
        # content is carried over untouched
        s = Skill.objects.get(source_ref="base-hr-job-description-writer")
        self.assertTrue(s.content.startswith("# Job Description Writer\n\n## What this does"))
        self.assertEqual(s.category.name, "HR")
        self.assertIn("Recruitment", s.tag_list)
        # the pilot guides keep their written text and land in sensible categories
        self.assertEqual(Skill.objects.get(source_ref="XL-01").category.name, "Excel")
        self.assertEqual(Skill.objects.get(source_ref="XL-01").subcategory.name, "Formula understanding")
        self.assertEqual(Skill.objects.get(source_ref="base-hr-job-description-writer").subcategory.parent.name, "HR")
        self.assertEqual(Skill.objects.get(source_ref="TRN-01").category.name, "Training & Learning")
        # second run changes nothing
        out2 = StringIO()
        call_command("import_library", stdout=out2)
        self.assertEqual(Skill.objects.count(), 72)
        self.assertIn("Imported ..................... 0", out2.getvalue())
        self.assertEqual(Category.objects.filter(parent__isnull=True).count(), 25)
        self.assertEqual(Category.objects.filter(parent__isnull=False).count(), 65)

    def test_import_library_with_packs_only_adds_duplicates(self):
        from io import StringIO
        Skill.objects.all().delete()
        Category.objects.all().delete()
        out = StringIO()
        call_command("import_library", "--include-packs", stdout=out)
        self.assertEqual(Skill.objects.count(), 72)
        self.assertIn("Imported ..................... 72", out.getvalue())

    def test_dry_run_writes_nothing(self):
        from io import StringIO
        Skill.objects.all().delete()
        call_command("import_library", "--dry-run", stdout=StringIO())
        self.assertEqual(Skill.objects.count(), 0)

    def test_html_is_not_accepted_through_the_upload_flow(self):
        _, report = self.analyse(("evil.html", b"<script>alert(1)</script>"))
        self.assertTrue(report.fatal)

    def test_standalone_page_is_parsed_as_data_not_executed(self):
        html = b'<html><script>const STARTER=[{"id":"base-x-one","title":"One","category":"X","subcategory":"Sub","level":"Quick","tags":["a"],"description":"d","content":"# One\\n\\nbody"}];alert(1)</script></html>'
        cands, report = importer.analyse_uploads([("page.html", html)], allow_html=True)
        self.assertEqual([(c["title"], c["source_ref"], c["category"]) for c in cands], [("One", "base-x-one", "X")])
        self.assertEqual(cands[0]["tags"], "a, Sub, Quick")


class ImportViewTests(BaseTestCase):
    def test_upload_review_commit_flow(self):
        self.login(self.admin)
        up = SimpleUploadedFile("new.md", b"# Brand New Skill\n\nbody", content_type="text/markdown")
        r = self.client.post("/admin/import/", {"files": up})
        self.assertEqual(r.status_code, 302)
        review = self.client.get(r["Location"])
        self.assertContains(review, "Brand New Skill")
        r2 = self.client.post(r["Location"], {"status": "published", "action_0": "import", "category_0": "Brand New Cat", "title_0": "Brand New Skill"})
        self.assertContains(r2, "Import summary")
        s = Skill.objects.get(title="Brand New Skill")
        self.assertEqual((s.status, s.category.name), ("published", "Brand New Cat"))
        self.assertEqual(self.client.get(r["Location"]).status_code, 302)  # staging removed

    def test_other_user_cannot_use_staged_import(self):
        self.login(self.admin)
        r = self.client.post("/admin/import/", {"files": SimpleUploadedFile("n.md", b"# Mine\n\nx")})
        self.client.logout()
        amit = self.make_user("amit@datasense.com", "admin")
        self.login(amit)
        self.assertEqual(self.client.get(r["Location"]).status_code, 302)
        self.assertFalse(Skill.objects.filter(title="Mine").exists())

    def test_bad_token_rejected(self):
        self.login(self.admin)
        self.assertIn(self.client.get("/admin/import/review/..%2F..%2Fetc/").status_code, (302, 404))
        self.assertIsNone(importer.load_stage("../../etc", self.admin))
        self.assertIsNone(importer.load_stage("0" * 32, self.admin))
