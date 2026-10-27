import re

from django.contrib.staticfiles import finders
from PIL import Image

from library.models import Category, Skill

from .helpers import BaseTestCase


class BrandingTests(BaseTestCase):
    def test_logo_assets_exist_with_original_proportions_and_no_distortion(self):
        full = Image.open(finders.find("img/logo-full.png"))
        icon = Image.open(finders.find("img/logo-d.png"))
        self.assertAlmostEqual(full.width / full.height, 1200 / 184, delta=0.02)  # the HTML width/height attributes match the file
        self.assertEqual(icon.width, icon.height)
        self.assertEqual(Image.open(finders.find("img/favicon.png")).size, (64, 64))
        self.assertEqual(Image.open(finders.find("img/apple-touch-icon.png")).size, (180, 180))

    def test_public_header_uses_full_logo_and_switches_to_icon_on_small_screens(self):
        body = self.client.get("/").content.decode()
        header = body.split("<header")[1].split("</header>")[0]
        self.assertIn('<source media="(max-width: 740px)" srcset="/static/img/logo-d.png">', header)
        self.assertRegex(header, r'<img class="brand-logo" src="/static/img/logo-full.png" alt="Datacense" width="1200" height="184">')
        self.assertIn('aria-label="Datacense AI Skill Library - home"', header)

    def test_footer_and_brand_banner_use_full_logo_with_alt_text(self):
        body = self.client.get("/").content.decode()
        footer = body.split("<footer")[1]
        self.assertIn('class="logo-chip"', footer)
        self.assertIn('src="/static/img/logo-full.png" alt="Datacense"', footer)
        banner = body.split('class="brand-banner"')[1].split("</section>")[0]
        self.assertIn('src="/static/img/logo-full.png" alt="Datacense"', banner)
        self.assertIn("published skill", banner)

    def test_every_image_has_alt_text(self):
        self.login(self.super)
        for url in ("/", "/skills/", "/favorites/", "/admin/login/", "/admin/dashboard/", "/admin/skills/"):
            for img in re.findall(r"<img[^>]*>", self.client.get(url, follow=True).content.decode()):
                self.assertIn("alt=", img, url)

    def test_favicon_links_on_public_login_and_management_pages(self):
        for url in ("/", "/admin/login/"):
            body = self.client.get(url).content.decode()
            self.assertIn('rel="icon" href="/static/img/favicon.png"', body)
            self.assertIn('rel="apple-touch-icon" href="/static/img/apple-touch-icon.png"', body)
        self.login(self.admin)
        self.assertIn('rel="icon" href="/static/img/favicon.png"', self.client.get("/admin/dashboard/").content.decode())

    def test_login_and_activation_pages_show_the_logo(self):
        for url in ("/admin/login/", "/admin/forgot-password/"):
            body = self.client.get(url).content.decode()
            self.assertIn("/static/img/logo-full.png", body, url)
            self.assertIn("logo-d.png", body, url)  # compact icon for narrow screens
        self.assertIn("/static/img/logo-full.png", self.client.get("/admin/activate/x/y/").content.decode())

    def test_management_sidebar_has_full_logo_and_topbar_has_compact_icon(self):
        self.login(self.admin)
        body = self.client.get("/admin/dashboard/").content.decode()
        side = body.split('id="console-side"')[1].split("</aside>")[0]
        self.assertIn('class="logo-chip side-logo"', side)
        self.assertIn("logo-full.png", side)
        top = body.split('class="console-top"')[1].split("</header>")[0]
        self.assertIn('class="top-icon"', top)
        self.assertIn('src="/static/img/logo-d.png" alt="Datacense"', top)

    def test_logos_are_never_restyled_by_css(self):
        css = open(finders.find("css/app.css"), encoding="utf-8").read()
        for rule in re.findall(r"\.(?:brand-logo|logo-chip img|top-icon img|brand-banner img)\{[^}]*\}", css):
            self.assertNotIn("filter", rule)
            self.assertNotIn("object-fit", rule)  # never stretched or cropped

    def test_header_is_light_with_navy_text_so_the_logo_reads_clearly(self):
        css = open(finders.find("css/app.css"), encoding="utf-8").read()
        self.assertRegex(css, r"\.site-header\{background:var\(--surface-white\)")
        self.assertIn(".logo-chip{display:inline-block;background:var(--surface-white)", css)  # logo sits on white inside dark areas


class HeroArtTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.svg = open(finders.find("img/hero-network.svg"), encoding="utf-8").read()

    def test_artwork_is_local_self_contained_and_light(self):
        self.assertTrue(self.svg.startswith("<svg"))
        self.assertNotIn("<text", self.svg)
        self.assertNotIn("<image", self.svg)
        self.assertNotRegex(self.svg.replace("http://www.w3.org/2000/svg", ""), r"https?://")
        self.assertLess(len(self.svg), 60_000)

    def test_artwork_is_clearly_visible(self):
        self.assertGreaterEqual(self.svg.count("<line"), 100)  # many connecting lines
        self.assertGreaterEqual(self.svg.count("<circle"), 150)  # nodes plus glows
        opacities = [float(x) for x in re.findall(r'<line[^>]*stroke-opacity="([\d.]+)"', self.svg)]
        self.assertGreaterEqual(min(opacities), 0.5)  # lines were ~.28 before; now >= .5
        for needle in ('id="gC"', 'id="gA"', 'id="sheen"', 'id="rim"', 'id="soft"', 'id="flow"', "#38BDF8", "#F5A623"):
            self.assertIn(needle, self.svg)  # glow, glass sheen, rim light, depth blur, flow curves, cyan + amber accents

    def test_hero_css_keeps_the_headline_readable_over_the_artwork(self):
        css = open(finders.find("css/app.css"), encoding="utf-8").read()
        self.assertIn(".hero-v2::before{", css)
        self.assertRegex(css, r"\.hero-v2::before\{[^}]*radial-gradient\(ellipse[^}]*rgba\(16,24,39,\.86\)")
        art = re.search(r"\.hero-art\{([^}]*)\}", css).group(1)
        self.assertNotIn("opacity", art)  # artwork is no longer faded out

    def test_generator_is_reproducible(self):
        import runpy
        from pathlib import Path
        script = Path(finders.find("img/hero-network.svg")).parents[2] / "tools" / "make_hero_svg.py"
        before = open(finders.find("img/hero-network.svg"), encoding="utf-8").read()
        runpy.run_path(str(script))
        self.assertEqual(open(finders.find("img/hero-network.svg"), encoding="utf-8").read(), before)


class HomeDiscoveryTests(BaseTestCase):
    def test_domain_cards_have_one_link_and_subcategory_chips(self):
        sub1 = Category.objects.create(name="Formulas", parent=self.cat)
        sub2 = Category.objects.create(name="Pivots", parent=self.cat)
        Skill.objects.create(title="F1", category=self.cat, subcategory=sub1, content="x", status="published")
        Skill.objects.create(title="F2", category=self.cat, subcategory=sub1, content="x", status="published")
        Skill.objects.create(title="P1", category=self.cat, subcategory=sub2, content="x", status="published")
        card = self.client.get("/").content.decode().split('class="domain-card"')[1].split("</div>\n")[0]
        self.assertIn(f'href="/categories/{sub1.slug}/">Formulas</a>', card)
        self.assertLess(card.index("Formulas"), card.index("Pivots"))  # biggest subcategory first
        self.assertIn(f'href="/categories/{self.cat.slug}/">Excel</a>', card)

    def test_explore_sidebar_has_expand_and_collapse_all(self):
        body = self.client.get("/skills/").content.decode()
        self.assertIn('data-tree="open"', body)
        self.assertIn('data-tree="close"', body)

    def test_brand_banner_never_shows_made_up_numbers_when_empty(self):
        Skill.objects.update(status="draft")
        body = self.client.get("/").content.decode()
        banner = body.split('class="brand-banner"')[1].split("</section>")[0]
        self.assertNotRegex(banner, r"\d+ published skill")
