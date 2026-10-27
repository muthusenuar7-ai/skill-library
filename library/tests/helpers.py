import io
import shutil
import tempfile
import zipfile

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import ROLE_ADMIN, ROLE_SUPER, User
from accounts.roles import set_role
from library.models import Category, Skill

PASSWORD = "Correct-Horse-Battery-9"  # test-only value, used only inside the throw-away test database


class BaseTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(PRIVATE_MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.cat = Category.objects.create(name="Excel")
        self.other_cat = Category.objects.create(name="Power BI")
        self.pub = Skill.objects.create(title="Formula Explainer", category=self.cat, content="# Formula Explainer\n\nExplains formulas.", description="Explains", status="published", tags="excel, formulas")
        self.draft = Skill.objects.create(title="Secret Draft", category=self.cat, content="draft body SECRETDRAFT", status="draft")
        self.archived = Skill.objects.create(title="Old Archived", category=self.cat, content="archived body SECRETARCHIVED", status="archived")
        self.super = self.make_user("muthu@datasense.com", ROLE_SUPER)
        self.admin = self.make_user("akhilesh@datasense.com", ROLE_ADMIN)

    def make_user(self, email, role):
        user = User.objects.create_user(email, password=PASSWORD)
        return set_role(user, role)

    def login(self, user):
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))


def make_zip(entries):
    """entries: {name: bytes|str}. Names are written verbatim (even unsafe ones)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()
