"""Datacense AI Skill Library - settings.

All deployment-specific values come from environment variables (or a local .env file).
manage.py defaults DJANGO_ENV=development; wsgi/asgi default to production.
"""
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


ENVIRONMENT = os.environ.get("DJANGO_ENV", "production")
DEBUG = env_bool("DJANGO_DEBUG", ENVIRONMENT == "development")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-development-only-key-never-use-in-production"
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set when DEBUG is off. "
            'Generate one with: python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"'
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "library",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *([] if DEBUG else ["whitenoise.middleware.WhiteNoiseMiddleware"]),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.PasswordChangeRequiredMiddleware",
    "config.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "accounts.User"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "library.context_processors.site",
            ],
        },
    },
]


def _database_from_url(url):
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+")[0]
    if scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        }
    if scheme == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": unquote(parsed.path.lstrip("/")) or BASE_DIR / "db.sqlite3"}
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")


if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": _database_from_url(os.environ["DATABASE_URL"])}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        }
    }

if len(sys.argv) > 1 and sys.argv[1] == "test":
    # Fast hasher for the throw-away test database only; real runs use Django's default PBKDF2.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Activation links stay valid this long (seconds). Default: 3 days.
PASSWORD_RESET_TIMEOUT = env_int("ACTIVATION_LINK_TIMEOUT", 3 * 24 * 3600)

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/admin/dashboard/"
LOGOUT_REDIRECT_URL = "/"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploaded skill files and import attachments live OUTSIDE any web-served path and are only
# delivered through views that check publication status and permissions.
PRIVATE_MEDIA_ROOT = Path(os.environ.get("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))

# ---- Sessions / cookies / HTTPS -------------------------------------------------------
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 8 * 3600)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

USE_HTTPS = env_bool("DJANGO_HTTPS", not DEBUG)
if USE_HTTPS:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", True)
    if env_bool("DJANGO_BEHIND_PROXY"):
        # Only enable when a trusted reverse proxy sets/strips X-Forwarded-Proto.
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---- E-mail (activation invitations). Without EMAIL_HOST the app shows a secure setup link instead. -----
EMAIL_CONFIGURED = bool(os.environ.get("EMAIL_HOST"))
if EMAIL_CONFIGURED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = env_int("EMAIL_PORT", 587)
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Datacense Skill Library <no-reply@datasense.com>")
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")  # used in invitation e-mails; falls back to the request host

# ---- Login throttling (see accounts/ratelimit.py) --------------------------------------
LOGIN_MAX_ATTEMPTS = env_int("LOGIN_MAX_ATTEMPTS", 5)  # per e-mail address
LOGIN_MAX_ATTEMPTS_PER_IP = env_int("LOGIN_MAX_ATTEMPTS_PER_IP", 20)
LOGIN_LOCKOUT_SECONDS = env_int("LOGIN_LOCKOUT_SECONDS", 15 * 60)

# ---- Import limits (see library/importer.py) --------------------------------------------
IMPORT_MAX_UPLOAD_BYTES = env_int("IMPORT_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
IMPORT_MAX_ENTRY_BYTES = env_int("IMPORT_MAX_ENTRY_BYTES", 5 * 1024 * 1024)
IMPORT_MAX_FILES = env_int("IMPORT_MAX_FILES", 500)
IMPORT_MAX_UNCOMPRESSED_BYTES = env_int("IMPORT_MAX_UNCOMPRESSED_BYTES", 50 * 1024 * 1024)
IMPORT_MAX_COMPRESSION_RATIO = env_int("IMPORT_MAX_COMPRESSION_RATIO", 200)
# My Collection / Favorites ZIP downloads
COLLECTION_MAX_SKILLS = env_int("COLLECTION_MAX_SKILLS", 100)
COLLECTION_MAX_BYTES = env_int("COLLECTION_MAX_BYTES", 50 * 1024 * 1024)
SKILL_UPLOAD_MAX_BYTES = env_int("SKILL_UPLOAD_MAX_BYTES", 5 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # non-file form data
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # import review has several fields per skill

SOURCE_LIBRARY_DIR = Path(os.environ.get("SOURCE_LIBRARY_DIR", BASE_DIR / "source_library"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
