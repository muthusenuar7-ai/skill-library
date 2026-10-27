# Datacense AI Skill Library

One Django project with exactly **two interfaces**:

| | Public Skill Library | Management interface |
|---|---|---|
| Address | `/` (no login, no registration) | `/admin/login/` (separate URL, not linked from the public site) |
| Who | anyone | Admin and Super Admin accounts (e-mail + password) |
| What | browse, search, filter, preview, copy, download (skill / category / selection / whole library if enabled) | one dashboard for both roles; controls and server-side permissions depend on the signed-in role |

There is a single login form. It has no role selector: the role is read from the account after sign-in.

Stack: Django 5.2 LTS, ORM/templates/auth, vanilla JS/CSS, `markdown` + `nh3` (sanitiser), WhiteNoise, Gunicorn. Tested on Python 3.11 / Django 5.2.18.

## Quick start

Production website: [Datacense AI Skill Library](https://datacense-skill-library.vercel.app/). Deployment uses Vercel's native Django runtime, persistent PostgreSQL and private Blob storage. See [Vercel deployment and operations](docs/VERCEL_DEPLOYMENT.md). SMTP is optional and must be configured for email delivery; production never logs reset tokens. Production uploads and collection exports currently have a 4 MiB content limit.

Windows details are in [SETUP_WINDOWS.md](SETUP_WINDOWS.md).
```
python -m venv .venv
pip install -r requirements.txt          # inside the activated venv
python manage.py migrate
python manage.py seed_team               # 5 accounts, no passwords; prints one-time activation links
python manage.py import_library          # the original library (72 skills), repeatable
python manage.py runserver
```

## Where the real library content comes from (source discovery)
Located in `reference/` of the project folder you supplied and copied to `source_library/original/`:

| File | Finding | Use |
|---|---|---|
| `Datacense_AI_Skill_Library_Standalone.html` | Complete library page: **62 skills**, 23 categories (25 "Original collection" + 37 "Expanded collection"), data embedded as JSON | **Primary source** - read as data (never executed) |
| `datacense-ai-skill-library.html` | Earlier edition of the same page: 25 skills, identical text to the 25 "Original collection" skills | Design reference only (fully contained in the Standalone) |
| `06 - Prompt Library.zip` | A small JS prompt-library app (37 prompts, 23 categories) plus its git history/docs | Its prompts correspond to the 37 "Expanded collection" skills; not imported separately |
| `dist/downloads/guides/*.md` (+ packs) | 10 written guides produced by the earlier catalog build | Kept (they were already in the database): copied to `source_library/guides/` |

`import_library` result: **72 source records -> 72 skills in 25 categories**, 0 duplicates on first run, 72 duplicates (nothing written) on the second run.
- 3 base/expanded pairs share a title but have different instructions, and 2 more pairs share a topic; both versions are kept (`...-expanded` slug; the 3 identical titles get "(Expanded)").
- Subcategory and level from the page become tags. The page's internal "prompt" type marker is dropped (everything is a Skill).
- The 10 guides' category "Training and Education" is merged into "Training & Learning".
- Skill text is stored as written (line endings normalised).
- The old 50 "Planned" catalog entries had no content and were not created.
- `--include-packs` also reads the 10 pack ZIPs (they only repeat the guides, so they import as duplicates).

## Local development sign-in and download names (latest)
- **Why nobody could sign in**: the five accounts existed, were active and had the right roles, but their passwords were never set (accounts are created without passwords and wait for an activation link that nobody had used).
- **Local development passwords:** `initialize_admin_passwords --development-only` requires private `DEV_ADMIN_PASSWORD_<FIRSTNAME>` environment variables, each at least 12 characters. No default passwords are provided. Prefer one-time activation links from `seed_team`; never run development password initialization on a deployed site.
- **First login**: a temporary password sets `must_change_password`. After signing in the user is sent to *Choose your own password*; every other management URL (and Django admin) redirects there until the new password (12+ characters, validated, different from the temporary one) is saved. The old password then stops working. Afterwards, *Change password* is in the profile menu.
- **Download file names** (one shared helper, `library/exports.py`): the skill title with `&` -> `and`, every space/separator/unsupported character -> a single hyphen, no leading/trailing hyphens, ASCII only, Windows reserved names (CON, NUL, COM1...) get `-Skill`, at most 70 characters. Same-name skills never overwrite each other: the later one gets its identifier (`Excel-Data-Cleaning.md`, `Excel-Data-Cleaning-SK0042.md`). The same names are used for single downloads, My Collection (`My-Selected-Skills.zip`), Favorites (`My-Favorites.zip`), category ZIPs (`Excel-Skills.zip`), filtered selections and the whole library (`All-Published-Skills.zip`). Markdown content is byte-for-byte what is stored. Administrator backups keep their full folder structure.


## Categories, My Collection and Favorites management (latest)
- **Categories page** (`/categories/`): one card per major domain (icon, name, published count) with its subcategories as separated rows (name + count). Domain and every subcategory are independent links (domain -> all its published skills including subcategories; subcategory -> only that one). Rows have no underline; hover/keyboard focus give a soft amber background, darker amber text, semibold weight and a small arrow (150-180 ms, no size change); the selected row keeps an amber accent. The same rule styles the Explore sidebar. A search box ("Search domains and subcategories...") filters the real cards live: a matching domain keeps all its subcategories, a matching subcategory keeps its parent domain, with a clear action and an empty state. Order is the configured display order, then alphabetical; counts are published skills only.
- **My Collection** (`/collection/`, no login): a Skill Library "basket". Every skill card in Explore has **Add to Collection** (a labelled checkbox that changes to "In Collection" with a check mark - not colour alone), separate from the favorites star. Selections live in the browser under `datacense.collection.v1` as `{v:1, ids:[...]}` (ids only), survive navigation, filters, paging and refresh, ignore duplicates, and fall back to in-memory state with a warning if storage is blocked. A compact sticky panel (hidden when empty) shows "N skills selected - Review Collection - Download ZIP - Clear". Explore has *Select All on This Page*, *Deselect All on This Page* and *Select All Matching Results (N)*, which asks the server for the count plus at most `COLLECTION_MAX_SKILLS` ids (never the whole library) and confirms before adding. The collection page groups skills by domain with Remove, Remove Selected, Clear Collection, Continue Browsing and **Download Selected as ZIP**; skills that were unpublished are reported, removed from the collection and never downloaded.
- **ZIP downloads** (`POST /collection/download/`, `POST /favorites/download/`, CSRF-protected): the server re-validates every id/slug against currently *published* skills, removes duplicates, enforces `COLLECTION_MAX_SKILLS` (default 100) and `COLLECTION_MAX_BYTES` (default 50 MB), builds the archive in a temporary file and streams it. **Every public ZIP is flat**: just `Skill-Title.md` files in the root - no domain/subcategory/skill folders, no `SKILL.md`, no manifest (see *Download file names* below). Skills that depend on supporting files are flagged in My Collection with a link to their complete package (`/skills/<slug>/package/`); the Markdown-only ZIP never claims to contain them.
- **My Favorites** (browser-only, stated on the page): select one/many/all, Remove Selected (n), Add Selected / Add All to Collection, Download All Favorites as ZIP, and Clear All Favorites behind a confirmation dialog ("Remove all N skills from your Favorites?"). Single-star removal never asks. Favorites and the collection use separate storage keys and clearing one never touches the other.

## Branding and hero refresh (latest)
- **Logos** (`static/img/logo-full.png`, `logo-d.png`, `favicon.png`, `apple-touch-icon.png`) are your supplied artwork, cropped to their content and resized only - never recoloured or stretched. The header is white so the navy/blue wordmark reads clearly; in dark areas (footer, management sidebar) the full logo sits on a white chip. Full logo: public header (>740 px), footer, login/reset/activation header, landing brand banner, management sidebar. "D" icon: public header and auth header on phones (via `<picture>`), management top bar on small screens, favicon and touch icon.
- **Hero artwork** (`static/img/hero-network.svg`, 27 KB, regenerate with `python tools/make_hero_svg.py`): navy base, ~60 bright nodes with cyan glow, brighter hub nodes with rings (three in amber to echo the brand accent), connecting lines at 50-80% opacity instead of ~25%, a blurred far layer for depth, luminous flow curves, a glassy diagonal sheen and a thin rim light. The headline stays readable because a centred radial navy scrim in CSS (`.hero-v2::before`) dims only the area behind the text; the artwork itself is no longer faded.
- Domain cards now show their most-populated subcategories as links; the Explore sidebar has Expand all / Collapse all.

## Experience redesign (latest)
- **Design system**: amber/navy tokens in the `:root` block of `static/css/app.css` (`--primary-navy`, `--accent-amber`, ...) are the single source for colour, type, spacing, radii and shadows on the public site and the management console. The earlier teal theme is gone. Icons come from one local SVG sprite (`static/img/icons.svg`).
- **Home** (`/`): compact nav (Home, Explore Skills, Categories, My Favorites, Help) -> hero "Discover Skills. Unlock Possibilities." with search and popular topics (the most common real tags) -> four floating statistic tiles -> Explore by Domain -> Featured Skills (hidden when none) -> Recently Added -> How It Works -> footer. Every number is a live count of published records; the network illustration is kept at reduced intensity.
- **Explore Skills** (`/skills/`): its own two-column workspace - left: All Skills, category search, expandable major domains with nested subcategories and counts; right: search, result count, sort (Recently Added, A-Z, Z-A), grid/list toggle, removable filter chips, Clear All Filters, pagination. Filters combine (domain + subcategory + search + topic) and live in the URL, so results are shareable and the Back-to-results link on a skill preview returns to the same filtered list. Below 860 px the sidebar becomes a slide-out panel (focus moved in, Tab trapped, Esc closes). No popularity sort: nothing records downloads or views.
- **Category hierarchy**: `Category.parent` (two levels: Major Domain -> Subcategory) and `Skill.subcategory`. `Skill.category` is still the major domain, so no existing assignment changed. Migration `library/0003_category_hierarchy` only adds columns/indexes/constraints (verified on a copy of the pre-change database: 72 skills, 25 categories, 6 featured flags intact). The real subcategories come from the source library (65 across the 72 skills) and were applied to existing skills by re-running `python manage.py import_library`, which fills in only missing subcategories.
- **Search**: server-side over title, description, tags, full content, reference, domain and subcategory (every word must match); paginated 12 per page; the query-count test confirms the number of database queries does not grow with the number of skills. For very large libraries (tens of thousands) switch the content match to PostgreSQL full-text search.
- **Favorites**: browser-only (stated on the page). The favorites page no longer embeds the library: it asks `/favorites/cards/` for just the saved slugs, so removed/unpublished skills are reported ("N saved skills are no longer available" with a Remove button) and duplicates are ignored.
- **Management**: Add/Edit Skill has Major domain + dependent Subcategory dropdowns (and "new domain/subcategory" boxes); import review lets you correct domain and subcategory per skill; Categories page shows the hierarchy and creates subcategories.
- **Help** and **About** are real pages; the footer links only to pages that exist (there are no legal pages, so none are linked).

## Landing page, theme, uploads and passwords (earlier changes)
- **Landing page** (`/`): hero with a locally built SVG network illustration (`static/img/hero-network.svg`, 14 KB, no remote images), search box wired to the database, Explore Skills / Browse Categories, **Featured** (only skills marked featured and published; the section is absent when none are), categories with live counts, Recently added, footer. All numbers come from published records. The old combined listing now lives at `/skills/`.
- **Design tokens** in `static/css/app.css` (`:root`): Navy `#102A43`, Teal `#0D9488`, Sky `#38BDF8`, background `#F5F8FC`, text `#334155`, cards `#FFFFFF`; shared type scale, spacing, radii, shadows, buttons, forms, tables, dialogs and focus rings for both the public site and the console. Filled buttons use a darker teal (`#0F766E`) because white text on `#0D9488` is below the WCAG contrast minimum.
- **Skill management**: Add Skill has *Option A: Create* and *Option B: Upload a .md* (read, pre-filled, reviewed, saved as Draft); Write/Preview tabs (sanitised server-rendered Markdown); optional new category; bulk Publish/Unpublish/Archive/Feature; import review shows duplicate title/slug/content, existing vs new categories, invalid files and supporting files with Skip / Replace / Rename.
- **Authentication**: login page = Email, Password, Sign In, Forgot Password. Forgot Password uses Django's built-in reset (signed, expiring, single-use links; throttled; same response whether or not the address exists; only active management accounts with a password receive a link). `python manage.py audit_accounts` reports the status of the five initial accounts.

## Roles
| | Public | Admin | Super Admin |
|---|---|---|---|
| Browse / search / download published skills | yes | yes | yes |
| Dashboard, Manage Skills, Add/Edit Skill, Preview, Import, Publish / Unpublish / Archive, bulk actions | - | yes | yes |
| Categories | - | view + add | view + add + edit/hide |
| Activity | - | skill/category activity + own sign-ins | everything |
| Team Management (create Admin/Super Admin, edit, change role, deactivate/reactivate, reset password, invitations) | - | **no** | yes |
| Settings (whole-library download on/off, home intro, footer) | - | **no** | yes |
| Backup & Export | - | **no** | yes |
| Django admin (`/django-admin/`) | - | no | **no** (framework superusers only) |

Roles are two Django Groups with explicit permissions (`accounts/roles.py`). **Super Admin is not Django's `is_superuser`.** Neither role has `is_staff`/`is_superuser`. `/django-admin/` is reachable only by a framework superuser created on purpose with `python manage.py createsuperuser`; that account has no management role and cannot open the dashboard.
Every management view is wrapped by `admin_permission_required` (default deny; anonymous -> sign-in, wrong role -> 403). Rules enforced on the server: an Admin can never change roles, even with forged form fields; nobody can change their own role or deactivate themselves; the last active Super Admin cannot be deactivated or demoted.

## Import
Admin > Import Skills accepts `.md` (one or many), `.zip`, and `.json` library exports (including this app's backups). Upload progress bar -> review page (edit title/category, Skip / Replace / Import as new for duplicates) -> save as Draft or Published -> results. Duplicate checks: content fingerprint, source reference, slug and title, also within the same upload. Safety: unsafe/absolute/traversal paths, symlinks, encrypted and oversized entries rejected and listed; OS metadata ignored; file-count, per-file, unpacked-size and compression-ratio limits; archive entries are read in memory, never extracted to archive-derived paths; nothing is executed; attachments live in `PRIVATE_MEDIA_ROOT` and are served only through permission-checked views.

## Security summary
PBKDF2 hashing, 12-char minimum; no stored/default passwords (accounts start unusable; single-use expiring activation links, e-mailed when SMTP is configured; hidden-prompt `set_admin_password` command as an alternative); CSRF on every POST; POST-only logout; login throttling (5 failures per e-mail / 20 per IP / 15 min); allow-list Markdown sanitising; strict CSP, nosniff, frame denial, no-store on management pages; secure cookies / HTTPS redirect / HSTS when DEBUG is off; secrets from environment; `check --deploy` passes (automated test).

## Tests
`python manage.py test` - 252 tests (landing page, uploads, import review, password reset, account audit were added to the list below): public access without login and no management controls on public pages; single login form; Super Admin and Admin sign-in and automatic role handling; every restricted URL for anonymous and Admin users; forged privilege fields; team create/edit/role change/deactivate/reactivate/reset/invitation (e-mail and link fallback); last-Super-Admin and self protections; skill create/edit/publish/unpublish/archive/bulk; draft/archived isolation across detail, list, search, selection and download routes; Markdown and ZIP import (folders, packs, packages with attachments, JSON, backup round-trip); unsafe archive entries; limits; duplicates; the real-library migration (72 skills, 25 categories, repeatable); category/selection/library ZIP downloads and the settings toggle; persistence across sessions; production checks.

## Known limitations
- "Manage assigned content" is implemented as: Admins manage all skills (there is no per-skill assignment).
- Admins can add categories (so imports can create them) but cannot edit/hide existing ones.
- Login throttling uses Django's cache (per process by default); use a shared cache for several workers (DEPLOYMENT.md).
- Favorites live in the visitor's browser (localStorage).
- Cloud object storage for uploads is documented, not built. E-mail invitations need SMTP settings and were tested with Django's in-memory mail backend, not a live mail server.
- The importer accepts `.html` only from the server-side `import_library` command, not from the web form.
- Visual checks were done in the built-in browser at desktop and 375 px widths on a throwaway database; no cross-browser or screen-reader audit was done.
- E-mail domain is `datasense.com`, exactly as supplied (brand elsewhere: "Datacense").

## Activity management (Super Admin only)

The **Activity** page lists date/time, administrator, action, related skill or resource and description, with filters (date range, administrator, activity type), search and 50-per-page pagination.

Super Admins (permission `library.clear_activity`, held only by the Super Admin group) get a **Manage Activity** panel:

- **Clear Selected** - archives the ticked records on the page.
- **Clear Older Records** - archives records before a chosen date (not in the future).
- **Clear All** - archives every eligible record.

Behaviour: clearing is a soft delete (`archived_at`/`archived_by`); nothing is physically removed. A dialog shows the record count ("Are you sure you want to clear N activity records? This action cannot be undone.") and the server re-checks that count inside a transaction; a mismatch clears nothing. Requests are POST-only with CSRF, and authorization is re-checked in the view and the service layer.

Protected records are never cleared by any option: user/role changes, password and dev-password events, settings changes, exports, and clearing events. Every clearing action is written to the append-only **Security audit trail** (`/admin/activity/audit/`: who, when, which option, how many, protected kept) and to the activity feed as a protected entry. Super Admins can tick "Include cleared records" to view archived rows.
