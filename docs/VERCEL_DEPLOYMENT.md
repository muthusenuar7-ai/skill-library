# Deploying this Django application on Vercel

The repository root is the Django project root: `manage.py`, `requirements.txt`, and `config/wsgi.py` are at that root. Vercel's native Django framework detects `WSGI_APPLICATION = "config.wsgi.application"`. `.python-version` selects Python 3.12; `vercel.json` selects Django and the Singapore function region. No legacy `@vercel/python` route shim is required.

Vercel collects static assets during its native Django build. `STATIC_ROOT` is configured and Vercel uses WhiteNoise's compressed manifest storage. Static build artifacts may be bundled/CDN-served; application records and uploaded files must be external.

## Required services

- A persistent PostgreSQL database (Neon Free is the selected Marketplace plan), connected to the project's production environment as `DATABASE_URL`.
- The connected private Vercel Blob store, with `BLOB_READ_WRITE_TOKEN` or the SDK's supported OIDC credentials. The storage adapter never publishes a public object URL. Application views continue checking roles or skill publication before opening files.
- Both uploaded originals/attachments and import manifests/supporting files use the private storage backend. Import review is a separate HTTP request; its staging data must not be stored on the function filesystem.
- Sessions are stored in PostgreSQL. On Vercel the login throttle cache also uses a PostgreSQL cache table, created during the controlled release step.

## Production environment

Set values through Vercel settings or `vercel env add`; secrets must be supplied through stdin or the dashboard, never command-line arguments or committed files.

| Variable | Required setting |
|---|---|
| `DJANGO_ENV` | `production` |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_SECRET_KEY` | High-entropy secret stored as a sensitive Vercel variable |
| `DATABASE_URL` | Provider connection string including `sslmode=require` |
| `PRIVATE_MEDIA_STORAGE` | `vercel_blob` |
| `BLOB_READ_WRITE_TOKEN` | Injected by the private storage connection; never public |
| `DJANGO_BEHIND_PROXY` | `True` |
| `DB_CONN_MAX_AGE` | `0`, suitable for serverless pooled connections |
| `COLLECTION_MAX_BYTES` | `4194304` for this deployment |
| `IMPORT_MAX_UPLOAD_BYTES` | `4194304` for this deployment |
| `SKILL_UPLOAD_MAX_BYTES` | `4194304` for this deployment |
| `SITE_URL` | Final verified HTTPS public origin |

Hosts and CSRF origins are taken from explicit Django environment settings plus Vercel's trusted `VERCEL_URL`, `VERCEL_BRANCH_URL`, and `VERCEL_PROJECT_PRODUCTION_URL` system variables. Add any custom domain explicitly. HTTPS redirects and secure cookies remain enabled. Vercel startup rejects debug mode, SQLite, and the local media backend.

Do not share the production database with untrusted preview branches. Provision a separate preview database and private store before enabling functional previews. Upload/export limits are conservatively capped below the standard request/response size ceiling; large exports/uploads require a separately designed streaming/direct-object workflow.

## Controlled release

1. Configure and verify external services and environment variables.
2. Privately transfer existing records from SQLite to the empty production PostgreSQL database. Never commit a database, fixture containing accounts, password hashes, tokens, or uploaded private files. Keep the local database intact. Verify the source/destination record counts, skill IDs and Markdown content after transfer. Copy any stored files to the connected private store as necessary.
3. Run `python manage.py migrate --noinput` against production through a trusted local/CI environment; create the shared cache with `python manage.py createcachetable datacense_cache`. Run migrations once per controlled release, never at request startup or on every serverless cold start.
4. Preserve existing account roles and password hashes during migration. For new installations use `seed_team` and distribute each one-time activation link privately. Never run `initialize_admin_passwords` in production. That development-only command requires private environment-supplied passwords and includes no predictable default.
5. Run `python manage.py check --deploy --fail-level WARNING` and the automated tests. Stage a production build with `vercel deploy --prod --skip-domain`, inspect the build, and verify it before assigning production domains.
6. Promote the tested production deployment and verify the actual public HTTPS URL: browsing, search, previews, static assets, Markdown and ZIP exports, favorites, login, role boundaries, import persistence, and attachment retrieval across requests.

Migrations do not set or reset account passwords. Existing first-login password-change flags are preserved. When SMTP is not configured, production uses Django's dummy email backend so password-reset tokens are not printed into function logs. Configure SMTP for actual password-reset email delivery; authorized Team Management setup/reset links remain available.

## Verification commands

```powershell
python -m pip install -r requirements-test.txt
python manage.py check
python manage.py test --noinput
node library/tests/collection-ui.test.cjs
```

The storage and configuration regressions cover shared staging data, owner isolation, expiry, invalid object paths, preserved PostgreSQL SSL options, and rejection of unsafe serverless configuration. Real Blob verification additionally writes a uniquely named private object, reads its exact bytes through an independent backend access, and deletes only that test object.

Official references: [Django on Vercel](https://vercel.com/docs/frameworks/full-stack/django), [Python runtime](https://vercel.com/docs/functions/runtimes/python), [private Blob storage](https://vercel.com/docs/vercel-blob/private-storage).
