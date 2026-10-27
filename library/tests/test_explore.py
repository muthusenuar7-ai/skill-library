import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from library.models import Category, Skill
from library.taxonomy import taxonomy_tree

from .helpers import BaseTestCase


class HierarchyFixtureMixin:
    def build(self):
        self.da = Category.objects.create(name="Data Analytics")
        self.pbi = Category.objects.create(name="Power BI Subs", parent=self.da)
        self.xl = Category.objects.create(name="Excel Subs", parent=self.da)
        self.biz = Category.objects.create(name="Business")
        self.fin = Category.objects.create(name="Finance", parent=self.biz)
        mk = lambda title, cat, sub=None, status="published", tags="", content="body": Skill.objects.create(title=title, category=cat, subcategory=sub, status=status, tags=tags, content=content, description=f"About {title}")
        self.dax = mk("DAX Measure Explainer", self.da, self.pbi, tags="dax, measures", content="Explain a DAX measure")
        self.dax2 = mk("DAX Debugger", self.da, self.pbi, tags="dax")
        self.pivot = mk("Pivot Helper", self.da, self.xl, tags="pivot", content="mentions DAX once")
        self.budget = mk("Budget Review", self.biz, self.fin, tags="budget")
        self.draft_dax = mk("Secret DAX Draft", self.da, self.pbi, status="draft", tags="dax")
        self.arch_dax = mk("Old DAX", self.da, self.pbi, status="archived")


class HierarchyModelTests(HierarchyFixtureMixin, BaseTestCase):
    def test_existing_categories_stay_valid_top_level_domains(self):
        self.assertTrue(Category.objects.filter(pk=self.cat.pk, parent__isnull=True).exists())
        self.assertIsNone(self.pub.subcategory)  # a skill without a subcategory is fine
        self.assertEqual(self.client.get(f"/skills/{self.pub.slug}/").status_code, 200)

    def test_only_two_levels(self):
        self.build()
        deep = Category(name="Too Deep", parent=self.pbi)
        with self.assertRaises(ValidationError):
            deep.full_clean()

    def test_domain_with_skills_cannot_become_a_subcategory(self):
        self.build()
        self.cat.parent = self.biz
        with self.assertRaises(ValidationError):
            self.cat.full_clean()

    def test_same_subcategory_name_allowed_under_different_domains_but_not_twice(self):
        self.build()
        Category.objects.create(name="Finance", parent=self.da)  # same name, other parent
        with self.assertRaises(IntegrityError):
            Category.objects.create(name="finance", parent=self.biz)

    def test_skill_never_keeps_a_subcategory_of_another_domain(self):
        self.build()
        s = Skill.objects.create(title="Cross", category=self.biz, subcategory=self.pbi, content="x")
        self.assertIsNone(s.subcategory)

    def test_subcategory_slug_includes_domain(self):
        self.build()
        self.assertEqual(self.pbi.slug, "data-analytics-power-bi-subs")

    def test_tree_counts_only_published(self):
        self.build()
        tree = {d.name: d for d in taxonomy_tree()}
        self.assertEqual(tree["Data Analytics"].published_total, 3)
        self.assertEqual({s.name: s.published_total for s in tree["Data Analytics"].subs}, {"Power BI Subs": 2, "Excel Subs": 1})


class ExploreTests(HierarchyFixtureMixin, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def titles(self, url):
        body = self.client.get(url).content.decode()
        return set(re.findall(r'<h3><a href="/skills/[^"]+">([^<]+)</a></h3>', body)), body

    def test_two_column_workspace_not_a_landing_page(self):
        body = self.client.get("/skills/").content.decode()
        self.assertIn('class="explore"', body)
        self.assertIn('class="filters-panel"', body)
        self.assertNotIn("hero-v2", body)
        self.assertNotIn("Unlock Possibilities", body)

    def test_sidebar_has_all_skills_expandable_domains_nested_subs_and_real_counts(self):
        body = self.client.get("/skills/").content.decode()
        tree = body.split('id="tree"')[1].split("</ul>\n    <p")[0]
        self.assertIn("All Skills", tree)
        self.assertEqual(tree.count("<details"), 3)  # Data Analytics, Business, Excel (existing flat domain with 1 published skill)
        m = re.search(r"Data Analytics</span><span class=\"number\">(\d+)</span>", tree)
        self.assertEqual(m.group(1), "3")
        self.assertIn("Power BI Subs", tree)
        self.assertIn('<span class="number">2</span>', tree)
        self.assertNotIn("Secret DAX Draft", tree)

    def test_domain_filter_then_subcategory_filter(self):
        titles, body = self.titles(f"/skills/?domain={self.da.slug}")
        self.assertEqual(titles, {"DAX Measure Explainer", "DAX Debugger", "Pivot Helper"})
        self.assertIn(f"<details open>", body)  # selected domain is expanded
        titles, body = self.titles(f"/skills/?domain={self.da.slug}&sub={self.pbi.slug}")
        self.assertEqual(titles, {"DAX Measure Explainer", "DAX Debugger"})
        self.assertIn('aria-current="true"', body)

    def test_combined_domain_sub_and_search(self):
        titles, _ = self.titles(f"/skills/?domain={self.da.slug}&sub={self.pbi.slug}&q=measure")
        self.assertEqual(titles, {"DAX Measure Explainer"})
        titles, _ = self.titles(f"/skills/?domain={self.da.slug}&q=dax")  # content match too
        self.assertEqual(titles, {"DAX Measure Explainer", "DAX Debugger", "Pivot Helper"})
        titles, _ = self.titles(f"/skills/?domain={self.biz.slug}&q=dax")
        self.assertEqual(titles, set())

    def test_search_covers_title_description_tags_domain_subcategory_and_content(self):
        for q, expect in (("Pivot", "Pivot Helper"), ("About Budget", "Budget Review"), ("measures", "DAX Measure Explainer"),
                          ("Business", "Budget Review"), ("Finance", "Budget Review"), ("once", "Pivot Helper")):
            self.assertIn(expect, self.titles(f"/skills/?q={q}")[0], q)

    def test_drafts_and_archived_never_in_filtered_results(self):
        for url in (f"/skills/?q=dax", f"/skills/?sub={self.pbi.slug}", f"/categories/{self.pbi.slug}/", f"/skills/?tag=dax"):
            titles, _ = self.titles(url)
            self.assertNotIn("Secret DAX Draft", titles, url)
            self.assertNotIn("Old DAX", titles, url)

    def test_sub_without_domain_infers_domain_and_mismatched_sub_is_ignored(self):
        titles, body = self.titles(f"/skills/?sub={self.pbi.slug}")
        self.assertEqual(titles, {"DAX Measure Explainer", "DAX Debugger"})
        titles, _ = self.titles(f"/skills/?domain={self.biz.slug}&sub={self.pbi.slug}")  # pbi is not under Business
        self.assertNotIn("Budget Review", titles - {"Budget Review"})
        self.assertTrue(titles <= {"DAX Measure Explainer", "DAX Debugger"})  # sub wins and domain is corrected to its parent

    def test_category_urls_still_work_for_domains_and_subcategories(self):
        self.assertEqual(self.titles(f"/categories/{self.da.slug}/")[0], {"DAX Measure Explainer", "DAX Debugger", "Pivot Helper"})
        titles, body = self.titles(f"/categories/{self.xl.slug}/")
        self.assertEqual(titles, {"Pivot Helper"})
        self.assertIn("Excel Subs", body)
        self.assertEqual(self.client.get("/categories/nope/").status_code, 404)

    def test_chips_remove_one_filter_and_clear_all(self):
        body = self.client.get(f"/skills/?domain={self.da.slug}&sub={self.pbi.slug}&q=dax").content.decode()
        chips = re.findall(r'<span class="fchip"><small>(\w+)</small>.*?href="([^"]+)"', body)
        kinds = {k: href for k, href in chips}
        self.assertEqual(set(kinds), {"Domain", "Subcategory", "Search"})
        self.assertNotIn("sub=", kinds["Subcategory"])
        self.assertIn("domain=", kinds["Subcategory"])
        self.assertIn("q=dax", kinds["Subcategory"])  # removing one filter keeps the others
        self.assertNotIn("domain=", kinds["Domain"])
        self.assertNotIn("sub=", kinds["Domain"])  # removing the domain also drops its subcategory
        self.assertIn("q=dax", kinds["Domain"])
        self.assertNotIn("q=", kinds["Search"])
        self.assertIn("Clear All Filters", body)
        self.assertNotIn("fchip", self.client.get("/skills/").content.decode())

    def test_sidebar_links_keep_search_context(self):
        body = self.client.get("/skills/?q=dax").content.decode()
        self.assertIn(f"domain={self.da.slug}", body)
        link = re.search(r'href="(/skills/\?[^"]*sub=' + self.pbi.slug + r'[^"]*)"', body).group(1)
        self.assertIn("q=dax", link)

    def test_sort_options(self):
        def order(sort):
            body = self.client.get(f"/skills/?domain={self.da.slug}&sort={sort}").content.decode()
            return re.findall(r'<h3><a href="/skills/[^"]+">([^<]+)</a></h3>', body)
        self.assertEqual(order("az"), sorted(order("az")))
        self.assertEqual(order("za"), sorted(order("za"), reverse=True))
        self.assertEqual(order("recent")[0], "Pivot Helper")  # created last of the published domain skills
        body = self.client.get("/skills/").content.decode()
        for label in ("Recently Added", "Alphabetical A-Z", "Alphabetical Z-A"):
            self.assertIn(label, body)
        self.assertNotIn("Popular", body.split('id="sort"')[1].split("</select>")[0])  # no popularity tracking exists

    def test_pagination_keeps_filters_and_scales(self):
        for i in range(30):
            Skill.objects.create(title=f"Bulk Skill {i:02d}", category=self.biz, subcategory=self.fin, content="x", status="published", tags="bulkdemo")
        body = self.client.get(f"/skills/?domain={self.biz.slug}&sort=az").content.decode()
        self.assertEqual(len(re.findall(r'<li data-fav-card', body)), 12)
        nxt = re.search(r'href="([^"]*page=2[^"]*)"', body).group(1)
        self.assertIn("domain=" + self.biz.slug, nxt)
        self.assertIn("sort=az", nxt)
        self.assertEqual(self.client.get("/skills/?page=999").status_code, 200)  # out of range -> last page

    def test_query_count_is_flat_for_large_libraries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        Skill.objects.bulk_create([Skill(title=f"Many {i}", slug=f"many-{i}", category=self.biz, subcategory=self.fin, content="c", status="published", tags="t") for i in range(300)])
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/skills/")
        self.assertLess(len(ctx), 14)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/")
        self.assertLess(len(ctx), 18)

    def test_list_view_and_grid_view(self):
        self.assertIn('class="cards list"', self.client.get("/skills/?view=list").content.decode())
        self.assertNotIn("cards list", self.client.get("/skills/").content.decode())

    def test_empty_results_offer_clear_filters(self):
        body = self.client.get("/skills/?q=zzzznothing").content.decode()
        self.assertIn("No skills match these filters", body)
        self.assertIn("Clear Filters", body)

    def test_search_term_preserved_in_input(self):
        self.assertIn('value="dax measure"', self.client.get("/skills/?q=dax+measure").content.decode())

    def test_cards_show_domain_subcategory_tags_and_actions_only(self):
        body = self.client.get(f"/skills/?q=Measure").content.decode()
        card = body.split("<article")[1].split("</article>")[0]
        for needle in ("Data Analytics", "Power BI Subs", "dax", "Preview", "Download", "data-fav="):
            self.assertIn(needle, card)

    def test_selection_download_respects_filters(self):
        import io, zipfile
        r = self.client.get(f"/downloads/selection/?domain={self.da.slug}&sub={self.pbi.slug}")
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertEqual(sorted(names), ["DAX-Debugger.md", "DAX-Measure-Explainer.md"])

    def test_subcategory_zip_download(self):
        import io, zipfile
        r = self.client.get(f"/downloads/category/{self.pbi.slug}/")
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()), 2)


class PreviewAndFavoritesTests(HierarchyFixtureMixin, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_preview_shows_hierarchy_actions_and_original_markdown(self):
        body = self.client.get(f"/skills/{self.dax.slug}/").content.decode()
        for needle in ("Data Analytics", "Power BI Subs", "Copy Instructions", "Download Skill", "Add to Favorites", 'id="raw-content"'):
            self.assertIn(needle, body)
        self.assertIn(f"/categories/{self.pbi.slug}/", body)

    def test_back_to_results_link_is_rebuilt_safely(self):
        import html
        from urllib.parse import parse_qs, urlsplit
        body = self.client.get(f"/skills/{self.dax.slug}/?back=domain%3Ddata-analytics%26q%3Ddax%26evil%3D1").content.decode()
        href = html.unescape(re.search(r'<a href="([^"]+)">&larr; Back to results', body).group(1))
        self.assertEqual(urlsplit(href).path, "/skills/")
        self.assertEqual(parse_qs(urlsplit(href).query), {"domain": ["data-analytics"], "q": ["dax"]})  # unknown parameters dropped
        self.assertNotIn("Back to results", self.client.get(f"/skills/{self.dax.slug}/").content.decode())
        evil = self.client.get(f"/skills/{self.dax.slug}/?back=http://evil.example/").content.decode()
        self.assertNotIn("evil.example", evil)

    def test_cards_carry_back_context(self):
        body = self.client.get("/skills/?q=dax").content.decode()
        self.assertIn("?back=q%3Ddax", body)

    def test_long_code_blocks_render_in_scrollable_pre(self):
        long = "```python\n" + "x = 1  # " + "very long line " * 40 + "\n```\n"
        s = Skill.objects.create(title="Long Code", category=self.biz, content=long, status="published")
        body = self.client.get(f"/skills/{s.slug}/").content.decode()
        self.assertIn("<pre>", body)

    def test_favorites_page_does_not_embed_the_library(self):
        body = self.client.get("/favorites/").content.decode()
        self.assertNotIn("DAX Measure Explainer", body)
        self.assertIn('data-url="/favorites/cards/"', body)
        self.assertIn("this browser only", body)

    def test_favorites_cards_endpoint_returns_only_published_and_reports_found(self):
        r = self.client.get(f"/favorites/cards/?slugs={self.dax.slug},{self.draft_dax.slug},{self.arch_dax.slug},gone-forever,%3Cscript%3E").json()
        self.assertEqual(r["found"], [self.dax.slug])
        self.assertIn("DAX Measure Explainer", r["html"])
        self.assertNotIn("Secret DAX Draft", r["html"])
        self.assertEqual(self.client.get("/favorites/cards/").json()["found"], [])
        many = ",".join(f"s{i}" for i in range(500))
        self.assertEqual(self.client.get(f"/favorites/cards/?slugs={many}").status_code, 200)

    def test_help_and_about_are_real_pages(self):
        self.assertContains(self.client.get("/help/"), "this browser only")
        self.assertContains(self.client.get("/about/"), "published skill")
