from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from library import importer
from library.models import Skill


class Command(BaseCommand):
    help = (
        "Import the original Datacense library from the source folder: the standalone library page "
        "(original/Datacense_AI_Skill_Library_Standalone.html, 62 skills) and guides/*.md (10 skills). "
        "Repeatable: skills already present (same content, source reference, slug or title) are counted as duplicates and not touched."
    )

    def add_arguments(self, parser):
        parser.add_argument("source", nargs="?", default=str(settings.SOURCE_LIBRARY_DIR), help="Folder containing original/, guides/ (and packs/) (default: source_library).")
        parser.add_argument("--status", choices=[s.value for s in Skill.Status], default="published", help="Status for imported skills (default: published).")
        parser.add_argument("--include-packs", action="store_true", help="Also read packs/*.zip (they only repeat the guides, so they show up as duplicates).")
        parser.add_argument("--dry-run", action="store_true", help="Analyse and report only; write nothing.")

    def handle(self, *args, **opts):
        src = Path(opts["source"])
        if not src.is_dir():
            raise CommandError(f"Source folder not found: {src}")
        uploads = []
        patterns = ["original/Datacense_AI_Skill_Library_Standalone.html", "guides/*.md"] + (["packs/*.zip"] if opts["include_packs"] else [])
        for pattern in patterns:
            uploads.extend((p.name, p.read_bytes()) for p in sorted(src.glob(pattern)))
        if not uploads:
            raise CommandError(f"No library sources found in {src} (expected original/*.html and guides/*.md)")

        candidates, report = importer.analyse_uploads(uploads, allow_html=True)
        found = len(candidates) + len(report.rejected)
        duplicates = sum(1 for c in candidates if c["dup"])
        if opts["dry_run"]:
            created = sum(1 for c in candidates if c["action"] == "import")
            skipped, errors, new_cats = duplicates, len(report.rejected) + sum(1 for c in candidates if c["errors"]), []
            self.stdout.write("DRY RUN - nothing written")
        else:
            result = importer.commit_candidates(None, candidates, status=opts["status"])
            created, skipped = result.imported, len(result.skipped)
            errors = len(result.errors) + len(report.rejected) + len(report.fatal)
            new_cats = result.categories_created
            for label, msg in result.errors:
                self.stderr.write(f"ERROR {label}: {msg}")
        classified, new_subs = importer.backfill_subcategories(candidates, apply=not opts["dry_run"])
        for path, reason in report.rejected:
            self.stderr.write(f"REJECTED {path}: {reason}")
        for name, reason in report.fatal:
            self.stderr.write(f"FAILED {name}: {reason}")

        self.stdout.write(
            "\nImport summary\n"
            f"  Source files read ............ {len(uploads)}\n"
            f"  Source records found ......... {found}\n"
            f"  Imported ..................... {created}\n"
            f"  Skipped (not imported) ....... {skipped}\n"
            f"  Duplicates detected .......... {duplicates}\n"
            f"  Errors / rejected ............ {errors}\n"
            f"  Categories created ........... {len(new_cats)}{' (' + ', '.join(new_cats) + ')' if new_cats else ''}\n"
            f"  Files ignored (not skills) ... {len(report.ignored)}\n"
            f"  Existing skills given a subcategory: {classified} ({len(new_subs)} new subcategories)"
        )
