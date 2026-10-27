# Windows setup (PowerShell)

Requires Python 3.10 or newer from python.org (3.12 recommended). Check with `python --version`.

## 1. Open the project folder
```powershell
cd "I:\New folder (2)\skills-library\datacense_django"
```

## 2. Create and activate a virtual environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If PowerShell blocks the script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y`, then activate again. The prompt now starts with `(.venv)`.

## 3. Install dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Database
Migrations are already included. Apply them (creates `db.sqlite3`):
```powershell
python manage.py migrate
```
Only if you later change a model: `python manage.py makemigrations` and then `migrate` again.

## 5. Create the team accounts securely
```powershell
python manage.py seed_team
```
This creates the two Super Admins (muthu@, vineet@) and three Admins (akhilesh@, himanshi@, amit@ at datasense.com) with **no password**, and prints a one-time activation link for each. Give each person their own link; they open it, choose their own password (12+ characters) and then sign in. Links work once and expire after 3 days (`ACTIVATION_LINK_TIMEOUT`). Re-run `seed_team` any time to get fresh links for people who have not activated yet; accounts that already have passwords are untouched.

Alternative for a Super Administrator on this PC (password typed in a hidden prompt, never stored in a file or shell history):
```powershell
python manage.py set_admin_password muthu@datasense.com
```
A Super Admin can issue invitations and password resets later from **Team Management** (the link is e-mailed if SMTP is configured, otherwise shown on screen).

### Quick local sign-in for development (temporary passwords)
```powershell
python manage.py initialize_admin_passwords --development-only
```
Requires private `DEV_ADMIN_PASSWORD_<FIRSTNAME>` environment variables for the five initial accounts, each at least 12 characters. No default passwords are included. Prefer the one-time activation flow above. This command refuses to run outside a verified local development instance and forces a password change at first sign-in.

### Check the account status at any time (read-only, never shows passwords)
```powershell
python manage.py audit_accounts
```
It lists, for the five initial accounts: exists, role, active, password configured (yes/no) and permissions OK.

### Set up passwords (pick one)
1. **Activation links** - `python manage.py seed_team` prints a one-time link per person without a password yet. Open it in Chrome, choose a password.
2. **Hidden prompt** - `python manage.py set_admin_password muthu@datasense.com` (typing is not shown).
3. **Forgot Password** on the sign-in page, once a password exists: with SMTP configured the link is e-mailed; locally (no SMTP) the e-mail text, including the link, is printed in the PowerShell window that runs the server.

## 6. Import the original library
```powershell
python manage.py import_library
```
Reads `source_library/original/Datacense_AI_Skill_Library_Standalone.html` (62 skills) and `source_library/guides/*.md` (10 skills): **72 skills in 25 major domains with 65 subcategories**. On a database imported before subcategories existed, running it again fills in the missing subcategories (nothing else is changed). Prints the summary (records found / imported / skipped / duplicates / errors / categories). Safe to run again. Options: `--dry-run`, `--status draft`, `--include-packs`.

## 7. Run the tests
```powershell
python manage.py test
```

## 8. Start the server and open it in Chrome
```powershell
python manage.py runserver
```
In a second PowerShell window (or just paste the address into Chrome):
```powershell
Start-Process chrome "http://127.0.0.1:8000/"
```
| Page | Address |
|---|---|
| Public library | http://127.0.0.1:8000/ |
| Management sign in (Admin and Super Admin, same form) | http://127.0.0.1:8000/admin/login/ |
| Dashboard (after sign-in) | http://127.0.0.1:8000/admin/dashboard/ |
| Django admin (framework superuser only, optional) | http://127.0.0.1:8000/django-admin/ |

Stop the server with `Ctrl+C`. To test the mobile layout in Chrome press `F12`, then `Ctrl+Shift+M`.

## Optional: .env file
```powershell
Copy-Item .env.example .env
```
Edit `.env` for local overrides. Never commit it.

## Troubleshooting
- `python` not found: reinstall Python and tick "Add python.exe to PATH", or use `py -3`.
- `ModuleNotFoundError`: the virtual environment is not active - repeat step 2.
- Port busy: `python manage.py runserver 8001`.
- Activation link says invalid: it was used or expired - run `python manage.py seed_team` again.

## Optional: Django admin break-glass account
Not needed for normal use. Create one only if you must use `/django-admin/`:
```powershell
python manage.py createsuperuser
```
This account cannot open the management dashboard; Super Admins never get these framework flags.

## Upgrading from the first version
Run `python manage.py migrate`. Existing Super Admins lose Django's `is_superuser`/`is_staff` flags automatically and keep their role through the "Super Admin" group; existing skills, categories and activity are kept. Run `python manage.py import_library` to add the 62-skill library; skills already present are skipped.

## Create and publish a skill
1. Sign in at http://127.0.0.1:8000/admin/login/.
2. **Add Skill** (sidebar). Option A: type title, description, category, tags and Markdown (use the Preview tab to check). Option B: choose a `.md` file, press *Read file*, review the filled-in form.
3. Leave *Publication status* on **Draft** and press *Save skill*. Draft skills are private.
4. When ready: **Manage Skills** -> *Publish* (confirm). It appears on the public site immediately. Tick *Show in Featured skills* (or bulk action "Feature on home page") to put it on the landing page.
5. Many files at once: **Import Skills** -> choose several `.md` files or a `.zip` -> review conflicts (Skip / Replace / Rename) -> import (Draft by default).
