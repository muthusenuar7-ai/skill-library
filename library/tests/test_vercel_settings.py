"""Production configuration fails closed before an unsafe serverless release."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

from django.test import SimpleTestCase


class VercelSettingsTests(SimpleTestCase):
    def configure(self, **changes):
        env = os.environ.copy()
        env.update(VERCEL="1", DJANGO_ENV="production", DJANGO_DEBUG="False",
                   DJANGO_SECRET_KEY=secrets.token_urlsafe(64), PRIVATE_MEDIA_STORAGE="vercel_blob",
                   DATABASE_URL="postgresql://test:unused@localhost/test?sslmode=require",
                   VERCEL_URL="skill-library-test.vercel.app")
        env.update(changes)
        return subprocess.run([sys.executable, "-c", (
            "from config import settings as s; "
            "assert not s.DEBUG; "
            "assert s.DATABASES['default']['ENGINE']=='django.db.backends.postgresql'; "
            "assert s.DATABASES['default']['OPTIONS']['sslmode']=='require'; "
            "assert 'skill-library-test.vercel.app' in s.ALLOWED_HOSTS; "
            "assert 'https://skill-library-test.vercel.app' in s.CSRF_TRUSTED_ORIGINS; "
            "assert s.PRIVATE_MEDIA_STORAGE=='vercel_blob'; "
            "assert s.SECURE_PROXY_SSL_HEADER==('HTTP_X_FORWARDED_PROTO','https'); "
            "print('configuration verified')"
        )], env=env, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)

    def test_complete_configuration_preserves_ssl_and_trusts_only_configured_hosts(self):
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_debug_mode_is_rejected(self):
        result = self.configure(DJANGO_DEBUG="True")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_DEBUG must be False", result.stderr)

    def test_missing_persistent_database_is_rejected(self):
        result = self.configure(DATABASE_URL="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("persistent PostgreSQL", result.stderr)

    def test_local_media_storage_is_rejected(self):
        result = self.configure(PRIVATE_MEDIA_STORAGE="filesystem")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("persistent uploads and import staging", result.stderr)
