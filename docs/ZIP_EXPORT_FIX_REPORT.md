# Collection ZIP investigation and regression report

Verified locally on 27 October 2026 in the existing Django project.

## Findings and limits of the root-cause evidence

The two original archives at `C:/Users/shree/Downloads/Compressed/My-Selected-Skills.zip` and `My-Selected-Skills_2.zip` contain eight records. Matching their Markdown bytes to the database identifies the missing record as **ID 132, Business Proposal Builder Workflow** (`Business-Proposal-Builder-Workflow.md`). The original visible nine-skill collection corresponds to IDs **75, 74, 76, 106, 137, 89, 97, 101, 132**. The original archives contain exactly that set minus 132.

**The precise cause of those historical eight-file downloads is not established.** Their original POST bodies and server state were not retained. Before modifying production code, both a Django test-client request and a real HTTP POST to the running server exported all nine original IDs, including 132, with exact database content. A clean in-app browser also completed Explore → add nine published skills → My Collection → Select all → download, producing nine files before the changes. This rules out a deterministic nine-published-records → eight-files failure in the inspected current exporter. It would be inaccurate to claim that an off-by-one loop, filename collision, an unavailable ID 132, or a download manager was proven responsible for those earlier files.

Independent inspection and failing regression tests did prove these defects:

| File/function | Proven behavior before correction | Correction |
|---|---|---|
| `library/views.py:collection_download`, `_parse_ids`, `_public_by_ids` | Malformed IDs are ignored; unavailable IDs are filtered out without telling the downloader. Nine requested IDs with one becoming unpublished yield eight Markdown files. The old parser also examines only the first 2,000 comma-separated tokens per field; a later distinct ID can disappear. | Download-specific validation examines every token, deduplicates by ID, checks positive signed-64-bit IDs and the configured unique-record limit, and rejects malformed/unavailable selections with an explicit message. No partial ZIP is returned. Display endpoints retain their existing bounded, forgiving parsing. |
| `static/js/app.js`: collection form submit handler | “Download Selected as ZIP” reads all IDs from localStorage, ignoring the checked collection rows. | The collection form uses its checked rows. Empty selection is disabled and blocked. The global collection-bar form continues downloading the saved collection. |
| `static/js/app.js`: `validateColl`, `loadCollectionPage` | A delayed validation response can overwrite newly added IDs. A delayed collection response can paint rows for an earlier selection. | Validation removes only the invalid IDs it actually checked; collection loads reject obsolete responses and reload changed selections. |
| `static/js/app.js`: `getItem`, `setItem` | A failed storage write followed by a successful read returns the older localStorage value instead of the latest memory value. | Keys whose writes failed use the in-memory state for the page session. |

No filename-helper or ZIP-writer defect was found. `assign_filenames` already uses skill-ID suffixes for normalized, case-insensitive collisions; `_Zip.add` also protects archive-name uniqueness. `_flat_markdown_zip` writes every supplied record directly from `Skill.content`, regardless of attachments. These helpers remain unchanged. Database queries use `Skill.objects.public()` (published status and active domain), preserve requested ID order, and apply no three-record or last-record slice. Homepage/pagination limits were not changed.

## Exact browser trace after correction

The clean browser selected nine distinct published skills across five domains, including the originally missing ID 132. The checked rows, actual browser POST captured by temporary local instrumentation, parsed IDs, database records, generation inputs, and byte-verified archive entries all agree on:

`142, 141, 137, 140, 139, 138, 132, 136, 135`

| ID | Archive filename |
|---|---|
| 142 | Formula-Debugger.md |
| 141 | Formula-Explainer.md |
| 137 | DAX-Context-Explainer.md |
| 140 | Practice-Exercise-Creator.md |
| 139 | Slide-Content-Builder.md |
| 138 | Session-Recap-Creator.md |
| 132 | Business-Proposal-Builder-Workflow.md |
| 136 | Meeting-Minutes-and-Action-Tracker.md |
| 135 | Professional-Email-Writer.md |

The browser was also tested with only ID 132 unchecked: exactly the other eight IDs were exported. All nine checkboxes were restored afterwards. Temporary request instrumentation was removed; there is no diagnostic write in the final view. The retained `browser-request.json` contains only public skill IDs, never cookies or credentials.

Artifacts in `docs/export-verification/`:

- `browser-proof.json`: actual browser request, server generation trace, filenames, IDs independently matched by Markdown bytes, and archive SHA-256 hashes.
- `trace.json`: ID-by-ID traces and contents verification using existing published records for all required sizes.
- `browser-nine.zip`: real browser download after correction.
- `browser-subset-eight.zip`: real browser download with exactly one row intentionally unchecked.
- `browser-baseline-nine.zip`, `collection-baseline-nine.zip`, `collection-live-baseline-nine.zip`: successful pre-change browser, Django-client, and running-server exports.
- `verified-nine.zip`: original nine-record collection exported by the corrected view; includes ID 132.
- `verified-1.zip` through the tested sizes, including `verified-25.zip`: real generated ZIP artifacts.
- `browser-nine.png`: screenshot showing all nine rows checked.
- `django-tests.log`, `collection-tests.log`, `export-regression-tests.log`, `javascript-tests.log`: executed check results.

## Regression results

Each successful archive was opened with Python `zipfile`, checked for CRC errors, compared by the exact expected filename set, mapped to the exact expected skill IDs, and compared byte-for-byte with the database content.

| Unique published IDs selected | Exported Markdown files | Result |
|---:|---:|---|
| 1 | 1 | PASS |
| 2 | 2 | PASS |
| 3 | 3 | PASS |
| 4 | 4 | PASS |
| 6 | 6 | PASS |
| 8 | 8 | PASS |
| 9 | 9 | PASS |
| 10 | 10 | PASS |
| 25 | 25 | PASS |

Additional coverage: normalized title collisions, case/spacing/punctuation equivalents, multiple domains, database-only content, repeated POST fields, comma-separated IDs, duplicate IDs, drafts, archived records, hidden domains, deleted records, malformed tokens, over-limit selections, more than 2,000 duplicate tokens followed by another ID, checked subsets, empty selection, storage write failure, and delayed validation/page responses.

- `python manage.py check`: no issues.
- `python manage.py test --noinput`: **292 tests passed**.
- `python manage.py test library.tests.test_export_regression library.tests.test_collection --noinput`: **56 tests passed**.
- `node library/tests/collection-ui.test.cjs`: **7 tests passed**, executing the actual shipped app.js event handlers in a small DOM fixture.
- Full browser journey: actually executed in the in-app browser; actual downloads inspected with `zipfile`.
- The first full-suite run encountered the existing branding tests' missing `Pillow` dependency. Pillow 13.0.0 was installed in the existing virtual environment; the subsequent full suite passed. The application's existing requirements file does not list this branding-test dependency.

All successful collection ZIPs have flat, hyphen-separated `.md` filenames at the archive root, preserve complete stored UTF-8 Markdown, and contain no category folders or manifest. Existing count/byte limits remain configured and raise explicit errors; selected downloads are never silently truncated.

## Changed source and test files

1. `library/views.py`: strict all-or-nothing collection download validation.
2. `static/js/app.js`: checked-row submission, download-button state, storage fallback, stale-response handling.
3. `templates/library/collection.html`: mark checked-row form and initialize its button disabled.
4. `library/tests/test_collection.py`: update obsolete expectations that previously accepted silent omissions.
5. `library/tests/test_export_regression.py`: nine integration regression tests covering the requested matrix and identity/content checks.
6. `library/tests/collection-ui.test.cjs`: seven JavaScript regression tests.
7. `tools/verify_collection_export.py`: repeatable read-only verification and evidence generation against existing published records.
8. This report and the evidence artifacts listed above.

To repeat the real-record verification from the Django directory:

```powershell
& .venv/Scripts/python.exe tools/verify_collection_export.py --ids 75,74,76,106,137,89,97,101,132
```

No existing skills, account passwords, role assignments, category structure, migrations, or administration features were changed. Tests ran in Django's disposable test database. Remaining uncertainty is limited to the historical cause of the two original eight-file archives; the normal nine-published-skill workflow passed both before and after this correction.
