# Deployment guide

Production: [Datacense AI Skill Library](https://datacense-skill-library.vercel.app/) on Vercel, project `skill-library` in `muthusenuar7-ais-projects`. The application uses persistent Neon PostgreSQL and private Vercel Blob storage, including storage for import review sessions. See [the Vercel deployment guide](docs/VERCEL_DEPLOYMENT.md) for the active setup, controlled migrations, security guards and operational limits. The generic Python-host instructions below remain useful for other hosts.

> **SQLite and local files are not suitable on hosts with ephemeral storage** (most PaaS free tiers and containers): the database and every uploaded file/attachment would be lost on each restart or deploy. Use PostgreSQL and a persistent disk (or object storage) in production.

## 1. Environment variables
| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | long random string (required). `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"` |
| `DJANGO_ALLOWED_HOSTS` | `skills.example.com` (comma separated) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://skills.example.com` |
| `DJANGO_BEHIND_PROXY` | `True` if the platform terminates HTTPS and sets `X-Forwarded-Proto` (Render/Railway/Heroku style) |
| `DATABASE_URL` | `postgres://USER:PASSWORD@HOST:5432/DBNAME` |
| `PRIVATE_MEDIA_ROOT` | path on a persistent disk, e.g. `/var/data/private_media` |
| `SITE_URL` | `https://skills.example.com` (used in invitation e-mails) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | SMTP details. Without `EMAIL_HOST` the Team Management page shows a one-time setup link instead of sending e-mail. |
| `DJANGO_DEBUG` | leave unset (False). `DJANGO_ENV` defaults to `production` under Gunicorn. |

Never put these in Git. HTTPS redirect, secure cookies and HSTS turn on automatically when DEBUG is off (`DJANGO_HTTPS=False` only for a plain-HTTP test box).

## 2. Build and release commands
```bash
pip install -r requirements-postgres.txt     # includes psycopg for PostgreSQL
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_team --base-url https://skills.example.com   # once; prints one-time activation links
python manage.py import_library                                     # once: the original 72-skill library; repeat-safe
```

## 3. Start command
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3 --access-logfile -
```
A `Procfile` with this line is included. Static files are served by WhiteNoise (no separate web server needed). Put the app behind HTTPS (the platform's TLS or Nginx/Caddy).

## 4. PostgreSQL pathway
No model changes are needed. Create a database and user, set `DATABASE_URL`, run `migrate`. To move existing SQLite data: `python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e sessions --indent 2 > data.json`, switch `DATABASE_URL`, `migrate`, `python manage.py loaddata data.json`. (Or simply re-run `import_library` and use Admin > Export / Import for admin-created skills.)

## 5. Persistent file storage
Uploaded originals and package attachments are written to `PRIVATE_MEDIA_ROOT` (outside the static/public tree; served only through permission-checked views).
- **Persistent disk (simplest):** mount a volume and point `PRIVATE_MEDIA_ROOT` at it.
- **Object storage (S3/Azure/GCS):** install `django-storages`, subclass/replace `library/storage.py: PrivateStorage` with the storage backend (private ACL, no public URLs). Models and views need no change because every file access goes through `FieldFile.open()`.
Back up the database and `PRIVATE_MEDIA_ROOT` together. Admin > **Export / back up library** (Super Admin) downloads a JSON/ZIP backup of every skill, including drafts and attachments, that the Import page can read back.

## 6. Shared cache for exact login throttling (recommended with several workers)
Add to settings (or an env-driven block) a database cache and run `python manage.py createcachetable`:
```python
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "django_cache"}}
```

## 7. Verify before go-live
```bash
DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=skills.example.com python manage.py check --deploy
```
Expect "no issues". Then: public home loads without login and shows no management links; `/admin/dashboard/` redirects to `/admin/login/`; a draft skill's URL returns 404 when logged out. Keep `/admin/login/` unlisted in public pages (it is not linked anywhere on the public site).

## 8. Operations
- Reset a password: Team Management > *Reset password* (Super Admin), or `python manage.py set_admin_password email`.
- Deactivate a leaver: Team Management > *Deactivate* (history is kept; the last active Super Admin cannot be deactivated).
- Clean stale import staging folders are removed automatically (older than 24 h) when a new import starts.
- Keep Django patched: `pip install -U "Django>=5.2.18,<5.3"` and re-run tests.
