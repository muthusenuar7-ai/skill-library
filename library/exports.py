"""Downloads and backups. Callers decide WHICH skills are included (public views pass .public()).

All public Markdown downloads share ONE naming helper (`normalize_stem` / `skill_filename` / `assign_filenames`) so a
skill has the same file name whether it is downloaded alone, from My Collection, from Favorites or from a category.
"""
import io
import json
import re
import tempfile
import unicodedata
import zipfile

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header

from .models import Skill
from .utils import safe_filename

# ----------------------------------------------------------------------------- file names
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_STEM = 70  # leaves room for "-SK0042" and ".md" inside every filesystem's limits


def normalize_stem(text, fallback="Skill"):
    """'Excel Formula Builder & Explainer' -> 'Excel-Formula-Builder-and-Explainer'.

    ASCII letters and digits only, words joined by single hyphens, no leading/trailing hyphen, never a Windows
    reserved device name, never longer than MAX_STEM, never empty.
    """
    text = str(text or "").replace("&", " and ").replace("+", " plus ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"['`]", "", text)  # "Don't" -> "Dont"
    text = re.sub(r"[^A-Za-z0-9]+", "-", text)  # spaces and every separator or unsupported character
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if len(text) > MAX_STEM:
        cut = text[:MAX_STEM]
        text = (cut.rsplit("-", 1)[0] if "-" in cut[40:] else cut).strip("-")
    if not text:
        text = re.sub(r"[^A-Za-z0-9]+", "-", str(fallback)).strip("-") or "Skill"
    if text.upper() in RESERVED_NAMES:
        text += "-Skill"
    return text


def skill_filename(skill):
    """Download name of one skill: its title, hyphenated, plus .md"""
    return normalize_stem(skill.title, skill.slug or f"Skill-{skill.pk}") + ".md"


def assign_filenames(skills):
    """[(skill, 'File-Name.md')] with case-insensitive collisions resolved by appending the skill's identifier."""
    used, out = set(), []
    for s in skills:
        stem = normalize_stem(s.title, s.slug or f"Skill-{s.pk}")
        name, n = stem, 1
        if name.lower() in used:
            name = f"{stem}-SK{s.pk:04d}"
        while name.lower() in used:
            n += 1
            name = f"{stem}-SK{s.pk:04d}-{n}"
        used.add(name.lower())
        out.append((s, name + ".md"))
    return out


def zip_name(label, suffix="Skills"):
    """'Excel' -> 'Excel-Skills.zip'"""
    return f"{normalize_stem(label, 'Library')}-{suffix}.zip"


# ----------------------------------------------------------------------------- responses
def attachment_response(data, filename, content_type):
    resp = HttpResponse(data, content_type=content_type)
    resp["Content-Disposition"] = content_disposition_header(True, safe_filename(filename, "download"))
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


def file_response(fieldfile, filename):
    resp = FileResponse(fieldfile.open("rb"), as_attachment=True, filename=safe_filename(filename, "download"), content_type="application/octet-stream")
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


class _Zip:
    """ZipFile wrapper guaranteeing safe, unique, forward-slash entry names (no directory entries are written)."""

    def __init__(self, spool=False):
        self.buf = tempfile.TemporaryFile() if spool else io.BytesIO()  # temporary file for large selections
        self.zf = zipfile.ZipFile(self.buf, "w", zipfile.ZIP_DEFLATED)
        self.names = set()
        self.stamp = timezone.now().timetuple()[:6]

    def add(self, name, data):
        parts = [safe_filename(p, "item") for p in name.split("/") if p not in ("", ".", "..")]
        name = "/".join(parts)
        base, n = name, 1
        while name.lower() in self.names:
            n += 1
            stem, dot, ext = base.rpartition(".")
            name = f"{stem}-{n}.{ext}" if dot else f"{base}-{n}"
        self.names.add(name.lower())
        info = zipfile.ZipInfo(name, date_time=self.stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        self.zf.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
        return name

    def finish_file(self):
        self.zf.close()
        self.buf.seek(0)
        return self.buf

    def finish(self):
        self.zf.close()
        return self.buf.getvalue()


# ----------------------------------------------------------------------------- Markdown ZIPs (flat)
class SelectionTooLarge(Exception):
    pass


def _flat_markdown_zip(skills, spool=False):
    """Every skill's Markdown, byte-for-byte as stored, directly in the archive root - no folders, no extra files."""
    skills = list(skills)
    declared = sum(len(s.content.encode("utf-8")) for s in skills)
    if declared > settings.COLLECTION_MAX_BYTES:
        raise SelectionTooLarge(f"The selection is larger than the {settings.COLLECTION_MAX_BYTES // (1024 * 1024)} MB download limit. Remove some skills and try again.")
    z = _Zip(spool=spool)
    for skill, filename in assign_filenames(skills):
        z.add(filename, skill.content)
    return z


def skills_zip(skills):
    """Flat ZIP (bytes) for category / filtered-selection / whole-library downloads."""
    items = skills.select_related("category", "subcategory").order_by("title", "pk") if hasattr(skills, "select_related") else skills
    return _flat_markdown_zip(items).finish()


def selection_zip(skills):
    """Flat ZIP (temporary file) for My Collection and Favorites. `skills` must already be published skills only."""
    return _flat_markdown_zip(skills, spool=True).finish_file()


def skill_package_zip(skill):
    """The COMPLETE original package of one skill: SKILL.md plus its supporting files in their folders."""
    z = _Zip()
    folder = normalize_stem(skill.title, skill.slug)
    z.add(f"{folder}/SKILL.md", skill.content)
    for att in skill.attachments.all():
        try:
            with att.file.open("rb") as fh:
                z.add(f"{folder}/{att.path}", fh.read())
        except (FileNotFoundError, OSError):
            continue
    return z.finish()


def zip_response(data, name):
    return attachment_response(data, name, "application/zip")


def zip_file_response(fileobj, filename):
    resp = FileResponse(fileobj, as_attachment=True, filename=filename, content_type="application/zip")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "no-store"
    return resp


# ----------------------------------------------------------------------------- administrator backups (keep full structure)
def library_dict(include_attachments=False):
    """Full export of every skill regardless of status (administrators only)."""
    from .models import Category

    skills = []
    for s in Skill.objects.select_related("category", "subcategory", "created_by").prefetch_related("attachments"):
        item = {
            "title": s.title, "slug": s.slug, "description": s.description, "category": s.category.name, "subcategory": s.subcategory.name if s.subcategory else "",
            "tags": s.tag_list, "content": s.content, "status": s.status, "is_featured": s.is_featured,
            "original_filename": s.original_filename, "source_ref": s.source_ref,
            "created_by": s.created_by.email if s.created_by else "", "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
        }
        if include_attachments:
            item["attachments"] = [{"path": a.path, "file": f"attachments/{s.slug}/{a.path}"} for a in s.attachments.all()]
        skills.append(item)
    return {
        "format": "datacense-skill-library", "version": 1, "exported_at": timezone.now().isoformat(),
        "categories": [{"name": c.name, "slug": c.slug, "parent": c.parent.name if c.parent_id else "", "description": c.description, "display_order": c.display_order, "is_active": c.is_active} for c in Category.objects.select_related("parent")],
        "skills": skills,
    }


def backup_json_response():
    data = json.dumps(library_dict(), indent=2, ensure_ascii=False)
    return attachment_response(data, f"datacense-skill-library-{timezone.now():%Y%m%d-%H%M}.json", "application/json")


def backup_zip_response():
    z = _Zip()
    z.add("library.json", json.dumps(library_dict(include_attachments=True), indent=2, ensure_ascii=False))
    for s in Skill.objects.select_related("category", "subcategory").prefetch_related("attachments"):
        z.add(f"skills/{s.status}/{s.category.slug}/{s.slug}.md", s.content)
        for a in s.attachments.all():
            with a.file.open("rb") as fh:
                z.add(f"attachments/{s.slug}/{a.path}", fh.read())
    return zip_response(z.finish(), f"datacense-skill-library-backup-{timezone.now():%Y%m%d-%H%M}.zip")
