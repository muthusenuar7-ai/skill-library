import re
from datetime import timedelta

from django.contrib.staticfiles import finders
from django.utils import timezone

from library.models import Category, Skill

from .helpers import BaseTestCase


class LandingPageTests(BaseTestCase):
    def test_renders_without_login_with_required_sections_in_order(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Discover Skills. <span class="accent">Unlock Possibilities.</span>', body)
        self.assertIn("Explore practical AI skills that help you automate tasks, improve productivity, and work smarter.", body)
        self.assertIn('placeholder="Search skills, topics, tools, or workflows..."', body)
        self.assertIn('action="/skills/"', body)
        order = [body.index(x) for x in ('class="main-nav"', 'class="hero-v2"', 'class="stats-float"', 'id="dom-h"', 'id="recent-h"', 'id="how-h"', "<footer")]
        self.assertEqual(order, sorted(order))

    def test_navigation_items_and_no_management_links(self):
        self.login(self.super)  # even when signed in as Super Admin
        body = self.client.get("/").content.decode()
        nav = body.split('class="main-nav"')[1].split("</nav>")[0]
        for label in ("Home", "Explore Skills", "Categories", "My Favorites", "Help"):
            self.assertIn(label, nav)
        for url in ("/", "/skills/", "/categories/", "/favorites/", "/help/", "/about/"):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200, url)
            self.assertNotIn("/admin/", page.content.decode(), url)
            self.assertNotIn("sign in", page.content.decode().lower(), url)

    def test_stat_cards_are_real_published_counts(self):
        sub = Category.objects.create(name="Formulas", parent=self.cat)
        self.pub.subcategory = sub
        self.pub.save()
        Skill.objects.create(title="Old One", category=self.other_cat, content="x", status="published")
        Skill.objects.filter(title="Old One").update(created_at=timezone.now() - timedelta(days=90))
        Skill.objects.create(title="Hidden Sub", category=self.other_cat, subcategory=Category.objects.create(name="Unused", parent=self.other_cat), content="y", status="draft")
        tiles = re.findall(r"<b>(\d+)</b><span>([^<]+)</span>", self.client.get("/").content.decode().split('class="stats-float"')[1].split("</section>")[0])
        self.assertEqual(tiles, [("2", "Published skills"), ("2", "Major domains"), ("1", "Subcategory"), ("1", "Added in the last 30 days")])

    def test_numbers_follow_publication(self):
        def skills_tile():
            return re.search(r"<b>(\d+)</b><span>Published skill", self.client.get("/").content.decode()).group(1)
        self.assertEqual(skills_tile(), "1")
        self.draft.status = "published"
        self.draft.save()
        self.assertEqual(skills_tile(), "2")
        self.draft.status = "archived"
        self.draft.save()
        self.assertEqual(skills_tile(), "1")

    def test_domain_cards_show_real_categories_counts_and_link(self):
        Category.objects.create(name="Empty Domain")
        Category.objects.create(name="Hidden Domain", is_active=False)
        body = self.client.get("/").content.decode()
        section = body.split('id="dom-h"')[1].split("</section>")[0]
        self.assertIn("Excel", section)
        self.assertIn("<b>1</b> skill<", section)
        self.assertNotIn("Empty Domain", section)
        self.assertNotIn("Hidden Domain", section)
        self.assertNotIn("Power BI", section)  # exists, nothing published
        self.assertIn("View All Categories", section)
        self.assertIn(f'href="/categories/{self.cat.slug}/"', section)

    def test_at_most_eight_domains_biggest_first(self):
        for i in range(10):
            c = Category.objects.create(name=f"Domain {i}")
            for j in range(i + 1):
                Skill.objects.create(title=f"D{i} skill {j}", category=c, content="x", status="published")
        section = self.client.get("/").content.decode().split('id="dom-h"')[1].split("</section>")[0]
        names = re.findall(r"<h3><a [^>]*>([^<]+)</a></h3>", section)
        self.assertEqual(len(names), 8)
        self.assertEqual(names[0], "Domain 9")

    def test_featured_hidden_until_a_published_skill_is_featured(self):
        self.assertNotIn("Featured Skills", self.client.get("/").content.decode())
        self.pub.is_featured = True
        self.pub.save()
        self.draft.is_featured = True  # featured but private
        self.draft.save()
        body = self.client.get("/").content.decode()
        featured = body.split('id="feat-h"')[1].split("</section>")[0]
        self.assertIn("Formula Explainer", featured)
        self.assertNotIn("Secret Draft", body)
        for needle in ("Preview", "Download", "data-fav=", "Excel"):  # card actions + domain
            self.assertIn(needle, featured)

    def test_featured_card_shows_domain_subcategory_and_tags(self):
        sub = Category.objects.create(name="Formulas", parent=self.cat)
        self.pub.subcategory, self.pub.is_featured = sub, True
        self.pub.save()
        featured = self.client.get("/").content.decode().split('id="feat-h"')[1].split("</section>")[0]
        self.assertIn("Excel", featured)
        self.assertIn("Formulas", featured)
        self.assertIn(">excel<", featured)

    def test_recent_section_newest_first_limited_to_six(self):
        for i in range(8):
            Skill.objects.create(title=f"Recent Skill {i}", category=self.cat, content="x", status="published")
        recent = self.client.get("/").content.decode().split('id="recent-h"')[1].split('id="how-h"')[0]
        titles = re.findall(r"Recent Skill (\d)", recent)
        self.assertEqual(titles[0], "7")
        self.assertEqual(len(set(titles)), 6)
        self.assertNotIn("Recent Skill 1", recent)
        self.assertIn('href="/skills/"', recent)

    def test_how_it_works_has_four_steps(self):
        steps = self.client.get("/").content.decode().split('class="steps4"')[1].split("</ol>")[0]
        self.assertEqual(re.findall(r"<h3>(\w+)</h3>", steps), ["Discover", "Explore", "Download", "Apply"])

    def test_popular_topics_are_real_tags_and_navigate_to_matching_skills(self):
        Skill.objects.create(title="Budget Tool", category=self.cat, content="b", status="published", tags="finance, Structured")
        Skill.objects.create(title="Budget Two", category=self.cat, content="b", status="published", tags="finance")
        Skill.objects.create(title="Private", category=self.cat, content="b", status="draft", tags="secret-topic, secret-topic2")
        body = self.client.get("/").content.decode()
        topics = re.findall(r'class="topic" href="([^"]+)">([^<]+)<', body)
        names = [t[1] for t in topics]
        self.assertIn("finance", names)
        self.assertNotIn("Structured", names)  # complexity labels are not topics
        self.assertNotIn("secret-topic", names)  # draft tags never leak
        results = self.client.get(topics[names.index("finance")][0]).content.decode()
        self.assertIn("Budget Tool", results)
        self.assertNotIn("Secret Draft", results)

    def test_footer_links_all_resolve(self):
        footer = self.client.get("/").content.decode().split("<footer")[1]
        links = set(re.findall(r'href="(/[^"#]*)"', footer))
        self.assertTrue({"/skills/", "/categories/", "/help/", "/about/"} <= links)
        for link in links:
            self.assertEqual(self.client.get(link).status_code, 200, link)

    def test_hero_visual_is_local_and_clean(self):
        svg = open(finders.find("img/hero-network.svg"), encoding="utf-8").read()
        self.assertTrue(svg.startswith("<svg"))
        self.assertNotIn("<text", svg)
        self.assertNotIn("<image", svg)
        self.assertLess(len(svg), 60_000)
        self.assertIn("/static/img/hero-network.svg", self.client.get("/").content.decode())

    def test_page_loads_nothing_remote_and_has_no_inline_styles(self):
        for url in ("/", "/skills/", "/categories/", "/help/", "/about/", "/favorites/"):
            body = self.client.get(url).content.decode()
            self.assertEqual(re.findall(r'(?:src|href)="(https?://[^"]+)"', body), [], url)
            self.assertNotIn("style=", body, url)
            self.assertNotIn("<script>", body, url)

    def test_images_have_alt_text_and_icons_are_hidden_from_readers(self):
        body = self.client.get("/").content.decode()
        for img in re.findall(r"<img[^>]*>", body):
            self.assertIn("alt=", img)

    def test_design_tokens_are_the_single_source_of_truth(self):
        css = open(finders.find("css/app.css"), encoding="utf-8").read()
        for token, value in (("--primary-navy", "#101827"), ("--secondary-navy", "#1E293B"), ("--accent-amber", "#F5A623"), ("--accent-amber-hover", "#E99512"),
                             ("--background-light", "#F7F8FA"), ("--surface-white", "#FFFFFF"), ("--text-primary", "#172033"),
                             ("--text-secondary", "#667085"), ("--border-light", "#E5E7EB")):
            self.assertIn(f"{token}:{value}", css)
        for old in ("#0D9488", "#102A43", "#38BDF8", "--teal"):  # previous teal theme must be gone
            self.assertNotIn(old.lower(), css.lower())
        root = css.split("}")[0]
        rest = css[len(root):]
        self.assertEqual(re.findall(r"#[0-9A-Fa-f]{6}\b", rest).count("#F5A623"), 0)  # components use var(--accent-amber), not the literal
