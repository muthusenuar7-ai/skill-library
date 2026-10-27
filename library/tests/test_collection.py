import io
import re
import zipfile

from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.test import Client, override_settings

from library.exports import assign_filenames, normalize_stem, skill_filename, zip_name
from library.models import Category, Skill, SkillAttachment

from .helpers import BaseTestCase


class CollectionFixture:
    def build(self):
        self.da = Category.objects.create(name="Data Analytics")
        self.xl = Category.objects.create(name="Excel Tools", parent=self.da)
        self.pbi = Category.objects.create(name="Power BI Tools", parent=self.da)
        self.fin = Category.objects.create(name="Finance", display_order=0)
        self.bud = Category.objects.create(name="Budgeting", parent=self.fin)
        mk = lambda t, c, s=None, st="published", **kw: Skill.objects.create(title=t, category=c, subcategory=s, status=st, content=f"# {t}\n\n- item\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('x')\n```\n", description=f"About {t}", **kw)
        self.a = mk("Excel Formula Builder & Explainer", self.da, self.xl)
        self.b = mk("DAX Measure Optimization", self.da, self.pbi)
        self.c = mk("Financial Forecasting", self.fin, self.bud)
        self.d = mk("Loose Domain Skill", self.fin)  # no subcategory
        self.draft = mk("Secret Draft Skill", self.da, self.xl, st="draft")
        self.arch = mk("Old Archived Skill", self.da, self.xl, st="archived")
        att = SkillAttachment(skill=self.a, path="references/guide.txt", size=5)
        att.file.save("guide.txt", ContentFile(b"guide"))
        att.save()

    def post_zip(self, ids, client=None):
        return (client or self.client).post("/collection/download/", {"ids": ",".join(str(i) for i in ids)})

    def zf(self, resp):
        return zipfile.ZipFile(io.BytesIO(b"".join(resp.streaming_content)))

    def names(self, resp):
        return self.zf(resp).namelist()


class FilenameHelperTests(BaseTestCase):
    def test_examples_from_the_specification(self):
        for title, expected in (
            ("Excel Formula Builder & Explainer", "Excel-Formula-Builder-and-Explainer"),
            ("DAX Measure Optimization", "DAX-Measure-Optimization"),
            ("Financial Forecasting", "Financial-Forecasting"),
            ("AI Agent Workflow", "AI-Agent-Workflow"),
            ("Sales & Marketing Strategy", "Sales-and-Marketing-Strategy"),
            ("Power BI / DAX Guide", "Power-BI-DAX-Guide"),
        ):
            self.assertEqual(normalize_stem(title), expected)

    def test_rules(self):
        self.assertEqual(normalize_stem("  many    spaces\t\there  "), "many-spaces-here")
        self.assertEqual(normalize_stem("a --- b -- c"), "a-b-c")
        self.assertEqual(normalize_stem("--Leading and trailing--"), "Leading-and-trailing")
        self.assertEqual(normalize_stem('Bad:*?"<>|\\/ chars'), "Bad-chars")
        self.assertEqual(normalize_stem("Don't Stop"), "Dont-Stop")
        self.assertEqual(normalize_stem("Café Résumé 2024"), "Cafe-Resume-2024")
        self.assertEqual(normalize_stem("C++ & C#"), "C-plus-plus-and-C")
        self.assertEqual(normalize_stem("../../etc/passwd"), "etc-passwd")
        self.assertEqual(normalize_stem("中文", fallback="my-slug"), "my-slug")  # nothing usable -> fallback
        self.assertEqual(normalize_stem("???", fallback="***"), "Skill")

    def test_windows_reserved_names_and_length(self):
        for name in ("CON", "nul", "Aux", "COM1", "lpt9", "PRN"):
            self.assertTrue(normalize_stem(name).endswith("-Skill"), name)
        self.assertEqual(normalize_stem("Console"), "Console")
        long = normalize_stem("word " * 60)
        self.assertLessEqual(len(long), 70)
        self.assertFalse(long.endswith("-"))
        self.assertLessEqual(len(skill_filename(Skill(title="x" * 400, slug="s"))), 74)

    def test_collisions_get_the_identifier_and_never_overwrite(self):
        s1 = Skill.objects.create(title="Excel Data Cleaning", category=self.cat, content="one")
        s2 = Skill.objects.create(title="excel  data cleaning", category=self.cat, content="two")  # differs only by case/spacing
        s3 = Skill.objects.create(title="Excel Data Cleaning!", category=self.cat, content="three")
        names = [n for _, n in assign_filenames([s1, s2, s3])]
        self.assertEqual(names[0], "Excel-Data-Cleaning.md")
        self.assertEqual(names[1], f"excel-data-cleaning-SK{s2.pk:04d}.md")
        self.assertEqual(len({n.lower() for n in names}), 3)

    def test_zip_names(self):
        self.assertEqual(zip_name("Excel"), "Excel-Skills.zip")
        self.assertEqual(zip_name("Sales & Marketing"), "Sales-and-Marketing-Skills.zip")


class CollectionDownloadTests(CollectionFixture, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_zip_is_flat_with_named_markdown_files_only(self):
        r = self.post_zip([self.a.pk, self.b.pk, self.c.pk, self.d.pk])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertIn('filename="My-Selected-Skills.zip"', r["Content-Disposition"])
        self.assertEqual(sorted(self.names(r)), ["DAX-Measure-Optimization.md", "Excel-Formula-Builder-and-Explainer.md", "Financial-Forecasting.md", "Loose-Domain-Skill.md"])

    def test_no_folders_no_generic_skill_md_no_manifest(self):
        names = self.names(self.post_zip([self.a.pk, self.b.pk, self.c.pk]))
        for n in names:
            self.assertNotIn("/", n)
            self.assertTrue(n.endswith(".md"))
            self.assertNotEqual(n.lower(), "skill.md")
        self.assertFalse(any(n.endswith("/") for n in names))  # no directory entries either

    def test_markdown_content_is_preserved_exactly(self):
        z = self.zf(self.post_zip([self.a.pk]))
        self.assertEqual(z.read("Excel-Formula-Builder-and-Explainer.md").decode("utf-8"), self.a.content)
        self.assertIn("```python", self.a.content)
        self.assertIn("| a | b |", z.read("Excel-Formula-Builder-and-Explainer.md").decode())

    def test_archive_is_valid_and_portable(self):
        z = self.zf(self.post_zip([self.a.pk, self.b.pk]))
        self.assertIsNone(z.testzip())
        for info in z.infolist():
            self.assertRegex(info.filename, r"^[A-Za-z0-9][A-Za-z0-9-]*\.md$")  # safe on Windows, macOS and Linux
            self.assertFalse(info.flag_bits & 0x1)  # not encrypted
            self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)  # the method Windows File Explorer opens natively

    def test_duplicate_titles_get_identifier_suffix(self):
        t1 = Skill.objects.create(title="Excel Data Cleaning", category=self.da, content="first", status="published")
        t2 = Skill.objects.create(title="Excel Data Cleaning", category=self.fin, content="second", status="published")
        z = self.zf(self.post_zip([t1.pk, t2.pk]))
        self.assertEqual(sorted(z.namelist()), sorted(["Excel-Data-Cleaning.md", f"Excel-Data-Cleaning-SK{t2.pk:04d}.md"]))
        self.assertEqual(z.read("Excel-Data-Cleaning.md"), b"first\n")
        self.assertEqual(z.read(f"Excel-Data-Cleaning-SK{t2.pk:04d}.md"), b"second\n")

    def test_supporting_files_are_not_in_the_markdown_zip_but_the_package_stays_available(self):
        r = self.post_zip([self.a.pk])
        self.assertEqual(self.names(r), ["Excel-Formula-Builder-and-Explainer.md"])
        self.assertEqual(r["X-Skills-With-Supporting-Files"], "1")
        self.assertNotIn("X-Skills-With-Supporting-Files", self.post_zip([self.b.pk]))
        pkg = self.client.get(f"/skills/{self.a.slug}/package/")
        self.assertEqual(pkg["Content-Type"], "application/zip")
        self.assertIn("Excel-Formula-Builder-and-Explainer-Package.zip", pkg["Content-Disposition"])
        z = zipfile.ZipFile(io.BytesIO(pkg.content))
        self.assertEqual(sorted(z.namelist()), ["Excel-Formula-Builder-and-Explainer/SKILL.md", "Excel-Formula-Builder-and-Explainer/references/guide.txt"])
        self.assertEqual(SkillAttachment.objects.filter(skill=self.a).count(), 1)  # stored files untouched

    def test_duplicates_ignored_and_client_extras_ignored(self):
        r = self.client.post("/collection/download/", {"ids": f"{self.a.pk},{self.a.pk}", "category": "Hacked", "path": "../../etc"})
        self.assertEqual(self.names(r), ["Excel-Formula-Builder-and-Explainer.md"])

    def test_draft_archived_and_unknown_ids_reject_the_download(self):
        r = self.post_zip([self.a.pk, self.draft.pk, self.arch.pk, 99999, "abc", -5])
        self.assertRedirects(r, "/collection/", fetch_redirect_response=False)
        self.assertIn("No ZIP was downloaded", str(list(r.wsgi_request._messages)[-1]))

    def test_only_unavailable_ids_gives_a_friendly_error_not_an_empty_zip(self):
        r = self.post_zip([self.draft.pk, self.arch.pk])
        self.assertRedirects(r, "/collection/", fetch_redirect_response=False)
        self.assertIn("no longer available", [m.message for m in r.wsgi_request._messages][0])
        self.assertEqual(self.client.post("/collection/download/", {"ids": ""}).status_code, 302)
        self.assertEqual(self.client.post("/collection/download/", {}).status_code, 302)

    def test_selection_and_size_limits(self):
        with override_settings(COLLECTION_MAX_SKILLS=2):
            r = self.post_zip([self.a.pk, self.b.pk, self.c.pk])
            self.assertIn("up to 2", [m.message for m in r.wsgi_request._messages][-1])
            self.assertEqual(self.post_zip([self.a.pk, self.b.pk]).status_code, 200)
        with override_settings(COLLECTION_MAX_BYTES=10):
            r = self.post_zip([self.a.pk])
            self.assertIn("download limit", [m.message for m in r.wsgi_request._messages][-1])

    def test_post_only_and_csrf_protected(self):
        self.assertEqual(self.client.get("/collection/download/").status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post("/collection/download/", {"ids": str(self.a.pk)}).status_code, 403)
        self.assertEqual(strict.post("/favorites/download/", {"slugs": self.a.slug}).status_code, 403)

    def test_hostile_titles_cannot_escape_the_archive(self):
        evil = Skill.objects.create(title="../../Windows/System32: <evil>|", category=self.fin, content="x", status="published")
        con = Skill.objects.create(title="CON", category=self.fin, content="y", status="published")
        names = self.names(self.post_zip([evil.pk, con.pk]))
        self.assertEqual(sorted(names), ["CON-Skill.md", "Windows-System32-evil.md"])

    def test_nothing_administrative_in_the_archive(self):
        self.a.created_by = self.admin
        self.a.save()
        z = self.zf(self.post_zip([self.a.pk]))
        blob = b"".join(z.read(n) for n in z.namelist()).decode(errors="ignore")
        for secret in (self.admin.email, "akhilesh", "pbkdf2", "created_by"):
            self.assertNotIn(secret, blob)

    def test_collection_is_not_server_state_so_downloading_changes_nothing(self):
        self.assertEqual(self.post_zip([self.a.pk]).status_code, 200)  # anonymous; nothing favorited
        self.assertEqual(self.post_zip([self.a.pk]).status_code, 200)  # repeatable


class OtherDownloadTests(CollectionFixture, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_individual_download_uses_the_title_based_name_and_exact_content(self):
        r = self.client.get(f"/skills/{self.a.slug}/download/")
        self.assertIn('filename="Excel-Formula-Builder-and-Explainer.md"', r["Content-Disposition"])
        self.assertEqual(r.content.decode(), self.a.content)

    def test_individual_download_of_unpublished_is_refused(self):
        for s in (self.draft, self.arch):
            self.assertEqual(self.client.get(f"/skills/{s.slug}/download/").status_code, 404)

    def test_category_zip_is_flat_and_named_after_the_category(self):
        r = self.client.get(f"/downloads/category/{self.da.slug}/")
        self.assertIn('filename="Data-Analytics-Skills.zip"', r["Content-Disposition"])
        self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()), ["DAX-Measure-Optimization.md", "Excel-Formula-Builder-and-Explainer.md"])
        sub = self.client.get(f"/downloads/category/{self.xl.slug}/")
        self.assertIn('filename="Excel-Tools-Skills.zip"', sub["Content-Disposition"])
        self.assertEqual(zipfile.ZipFile(io.BytesIO(sub.content)).namelist(), ["Excel-Formula-Builder-and-Explainer.md"])

    def test_whole_library_and_selection_zips_are_flat(self):
        names = zipfile.ZipFile(io.BytesIO(self.client.get("/downloads/all/").content)).namelist()
        self.assertEqual(len(names), 5)  # the four published fixture skills + the base fixture's published skill
        self.assertTrue(all("/" not in n for n in names))
        r = self.client.get(f"/downloads/selection/?domain={self.fin.slug}")
        self.assertIn("Selected-Skills.zip", r["Content-Disposition"])
        self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()), ["Financial-Forecasting.md", "Loose-Domain-Skill.md"])

    def test_admin_download_uses_the_same_naming(self):
        self.login(self.admin)
        self.assertIn('filename="Secret-Draft-Skill.md"', self.client.get(f"/admin/skills/{self.draft.pk}/download/")["Content-Disposition"])

    def test_administrator_backup_keeps_its_full_structure(self):
        self.login(self.super)
        z = zipfile.ZipFile(io.BytesIO(self.client.get("/admin/export/").content))
        self.assertIn("library.json", z.namelist())
        self.assertTrue(any(n.startswith("skills/draft/") for n in z.namelist()))  # backups include drafts and folders


class CollectionEndpointTests(CollectionFixture, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_items_group_by_domain_and_report_unavailable_ids(self):
        Category.objects.filter(pk=self.da.pk).update(display_order=3)
        r = self.client.get(f"/collection/items/?ids={self.a.pk},{self.b.pk},{self.c.pk},{self.draft.pk},{self.a.pk},777,zzz").json()
        self.assertEqual(r["valid"], [self.a.pk, self.b.pk, self.c.pk])
        self.assertEqual(sorted(r["invalid"]), sorted([self.draft.pk, 777]))
        html = r["html"]
        self.assertLess(html.index("Finance"), html.index("Data Analytics"))
        for needle in ("Excel Formula Builder", "Excel Tools", "Power BI Tools", "About Excel Formula", "data-coll-remove", "Remove"):
            self.assertIn(needle, html)
        self.assertNotIn("Secret Draft Skill", html)

    def test_skills_with_supporting_files_are_flagged_with_a_package_link(self):
        html = self.client.get(f"/collection/items/?ids={self.a.pk},{self.b.pk}").json()["html"]
        self.assertEqual(html.count("Has supporting files"), 1)
        self.assertIn(f"/skills/{self.a.slug}/package/", html)

    def test_check_returns_only_published_ids(self):
        self.assertEqual(self.client.get(f"/collection/check/?ids={self.a.pk},{self.arch.pk},{self.draft.pk}").json()["valid"], [self.a.pk])

    def test_match_respects_filters_limit_and_visibility_without_loading_everything(self):
        Skill.objects.bulk_create([Skill(title=f"Bulk {i}", slug=f"bulk-{i}", category=self.fin, subcategory=self.bud, content="x", status="published", tags="bulk") for i in range(60)])
        Skill.objects.create(title="Hidden Bulk", category=self.fin, subcategory=self.bud, content="x", status="draft", tags="bulk")
        with override_settings(COLLECTION_MAX_SKILLS=25):
            r = self.client.get(f"/collection/match/?domain={self.fin.slug}&sub={self.bud.slug}&q=bulk").json()
        self.assertEqual((r["count"], len(r["ids"]), r["limit"]), (60, 25, 25))
        self.assertNotIn(Skill.objects.get(title="Hidden Bulk").pk, r["ids"])
        self.assertEqual(sorted(self.client.get(f"/collection/match/?domain={self.da.slug}").json()["ids"]), sorted([self.a.pk, self.b.pk]))

    def test_hostile_inputs_are_ignored_safely(self):
        for url in ("/collection/items/?ids=" + ",".join(["9" * 30] * 50), "/collection/items/?ids=1;DROP TABLE", "/collection/check/?ids=-1,0,1.5,%00", "/collection/items/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_collection_page_is_public_and_explains_the_markdown_only_zip(self):
        body = self.client.get("/collection/").content.decode()
        for needle in ("My Collection", "Continue Browsing", "Remove Selected", "Clear Collection", "Download Selected as ZIP", "no folders", "this browser only"):
            self.assertIn(needle, body)
        self.assertNotIn("/admin/", body)


class FavoritesZipAndUiTests(CollectionFixture, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_favorites_zip_is_flat_named_and_limited_to_available_favorites(self):
        r = self.client.post("/favorites/download/", {"slugs": f"{self.a.slug},{self.c.slug},{self.draft.slug},{self.arch.slug},gone,<script>"})
        self.assertEqual(r.status_code, 200)
        self.assertIn('filename="My-Favorites.zip"', r["Content-Disposition"])
        self.assertEqual(sorted(self.names(r)), ["Excel-Formula-Builder-and-Explainer.md", "Financial-Forecasting.md"])

    def test_favorites_zip_errors_and_limits(self):
        self.assertRedirects(self.client.post("/favorites/download/", {"slugs": self.draft.slug}), "/favorites/", fetch_redirect_response=False)
        with override_settings(COLLECTION_MAX_SKILLS=1):
            self.assertEqual(self.client.post("/favorites/download/", {"slugs": f"{self.a.slug},{self.c.slug}"}).status_code, 302)
        self.assertEqual(self.client.get("/favorites/download/").status_code, 405)

    def test_favorite_cards_support_bulk_selection_and_expose_ids_for_the_collection(self):
        r = self.client.get(f"/favorites/cards/?slugs={self.a.slug},{self.c.slug}").json()
        self.assertEqual(r["ids"], {self.a.slug: self.a.pk, self.c.slug: self.c.pk})
        for needle in (f'data-fav-select="{self.a.slug}"', f'data-id="{self.a.pk}"', f'data-collect="{self.a.pk}"'):
            self.assertIn(needle, r["html"])

    def test_favorites_page_has_every_bulk_control(self):
        body = self.client.get("/favorites/").content.decode()
        for label in ("Select All Favorites", "Remove Selected", "Add Selected to Collection", "Add All to Collection", "Clear All Favorites", "Download All Favorites as ZIP"):
            self.assertIn(label, body)

    def test_javascript_keeps_favorites_and_collection_state_separate_and_versioned(self):
        js = open(finders.find("js/app.js"), encoding="utf-8").read()
        self.assertIn("'datacense.collection.v1'", js)
        self.assertIn("'datacense.favorites'", js)
        coll_fns = re.search(r"function writeColl.*?\n", js).group(0) + re.search(r"function collRemove.*?\n", js).group(0)
        self.assertNotIn("FAV_KEY", coll_fns)
        clear_fav = js[js.index("getElementById('fav-clear')"):js.index("getElementById('fav-clear')") + 600]
        self.assertNotIn("writeColl", clear_fav)
        self.assertIn("Remove all ", js)
        self.assertIn("Clear All Favorites", js)


class PublicUiTests(CollectionFixture, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_navigation_has_my_collection_with_counter_and_favorites(self):
        body = self.client.get("/").content.decode()
        nav = body.split('class="main-nav"')[1].split("</nav>")[0]
        for label in ("Home", "Explore Skills", "Categories", "My Collection", "My Favorites", "Help"):
            self.assertIn(label, nav)
        self.assertLess(nav.index("My Collection"), nav.index("My Favorites"))
        self.assertEqual(body.count("data-coll-count"), 2)
        self.assertIn('class="cart-chip"', body)

    def test_cards_have_collection_checkbox_plus_existing_actions(self):
        card = self.client.get("/skills/").content.decode().split("<article")[1].split("</article>")[0]
        for needle in ("data-collect=", "Add to Collection", "Preview", "Download", "data-fav="):
            self.assertIn(needle, card)

    def test_explore_has_page_and_matching_selection_controls(self):
        body = self.client.get(f"/skills/?domain={self.da.slug}").content.decode()
        for label in ("Select All on This Page", "Deselect All on This Page", "Select All Matching Results (2)"):
            self.assertIn(label, body)
        self.assertIn(f'data-match-url="/collection/match/?domain={self.da.slug}"', body)

    def test_sticky_panel_and_dialog_exist_on_public_pages_but_not_on_login(self):
        body = self.client.get("/skills/").content.decode()
        panel = body.split('id="collection-bar"')[1].split("</div>\n<div class=\"toast\"")[0]
        for needle in ("Review", "Download ZIP", "Clear", "csrfmiddlewaretoken"):
            self.assertIn(needle, panel)
        self.assertIn(" hidden>", body.split('id="collection-bar"')[1][:200])
        self.assertIn('id="confirm-dialog"', body)
        self.assertNotIn("collection-bar", self.client.get("/admin/login/").content.decode())

    def test_collection_limit_published_to_javascript(self):
        with override_settings(COLLECTION_MAX_SKILLS=7):
            self.assertIn('<meta name="collection-limit" content="7">', self.client.get("/").content.decode())


class CategoriesPageTests(CollectionFixture, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()
        self.css = open(finders.find("css/app.css"), encoding="utf-8").read()

    def rule(self, selector):
        m = re.search(re.escape(selector) + r"\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, selector)
        return m.group(1)

    def test_subcategory_rows_are_not_underlined_and_use_a_single_separator(self):
        self.assertIn("text-decoration:none", self.css.split(".subcategory-link,.tree .sublist a{")[1].split("}")[0])
        self.assertNotIn("underline", self.rule(".subcategory-link:hover,.tree .sublist a:hover"))
        self.assertIn("border-top:1px solid var(--border-light)", self.rule(".sub-rows li+li"))
        self.assertNotIn("border-bottom", self.rule(".sub-rows li"))

    def test_hover_focus_and_selected_states(self):
        hover = self.rule(".subcategory-link:hover,.tree .sublist a:hover")
        self.assertIn("background:var(--amber-tint)", hover)
        self.assertIn("font-weight:600", hover)
        base = self.css.split(".subcategory-link,.tree .sublist a{")[1].split("}")[0]
        for needle in ("transition:background-color .18s ease,color .18s ease", "cursor:pointer", "font-weight:400"):
            self.assertIn(needle, base)
        self.assertNotRegex(hover, r"font-size")
        self.assertIn("outline:2px solid var(--accent-amber)", self.css)
        self.assertIn('.subcategory-link[aria-current="page"]', self.css)
        self.assertIn("--amber-tint:#FFF3DC", self.css)
        self.assertIn("--amber-text:#9A5800", self.css)

    def test_domain_and_each_subcategory_are_independent_links_with_published_counts(self):
        body = self.client.get("/categories/").content.decode()
        self.assertIn(f'href="/categories/{self.da.slug}/"', body)
        self.assertIn(f'href="/categories/{self.xl.slug}/"', body)
        card = body.split('data-name="data analytics"')[1].split("</section>")[0]
        self.assertRegex(card, r'class="number">2</span>')
        self.assertRegex(card.split("Excel Tools")[1], r'class="number">1</span>')
        self.assertNotIn("Secret Draft", body)

    def test_clicking_domain_and_subcategory_filter_correctly(self):
        dom = self.client.get(f"/categories/{self.da.slug}/").content.decode()
        self.assertIn("Excel Formula Builder", dom)
        self.assertIn("DAX Measure Optimization", dom)
        self.assertNotIn("Financial Forecasting", dom)
        titles = re.findall(r'<h3><a href="/skills/[^"]+">([^<]+)</a></h3>', self.client.get(f"/categories/{self.pbi.slug}/").content.decode())
        self.assertEqual(titles, ["DAX Measure Optimization"])

    def test_search_box_empty_state_and_clear_action_are_rendered(self):
        body = self.client.get("/categories/").content.decode()
        for needle in ('placeholder="Search domains and subcategories..."', 'id="cat-filter-clear"', 'id="cat-empty"', "No domains or subcategories match", 'data-name="power bi tools"'):
            self.assertIn(needle, body)
        js = open(finders.find("js/app.js"), encoding="utf-8").read()
        self.assertIn("a matching domain keeps all its subcategories", js)
        self.assertIn("a parent domain stays when one of its subcategories matches", js)

    def test_ordering_follows_display_order_then_name(self):
        for name in ("Alpha Domain", "Zulu Domain"):
            c = Category.objects.create(name=name, display_order=5)
            Skill.objects.create(title=name + " skill", category=c, content="x", status="published")
        names = re.findall(r'<span class="domain-name">([^<]+)</span>', self.client.get("/categories/").content.decode())
        self.assertLess(names.index("Finance"), names.index("Alpha Domain"))
        self.assertLess(names.index("Alpha Domain"), names.index("Zulu Domain"))
