"""Skill import engine (parsing, safety checks, duplicate detection, staging, commit).

Supported inputs
  * .md files (one or many)
  * .zip archives containing .md files, optionally in folders (folder name -> category)
  * the Datacense "Project Pack" layout: guides/*.txt (+ START_HERE.txt etc., which are ignored)
  * packaged skills: a folder holding SKILL.md plus supporting files (kept as attachments)
  * JSON library exports produced by this application (library.json)

Nothing from an upload is ever executed or extracted to a path derived from an archive entry:
entries are read into memory (with size caps) and attachments are stored under generated names.
"""
import io
import json
import re
import shutil
import stat
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from .activity import log_activity
from .markdown_utils import FRONT_MATTER
from .models import Category, Skill, SkillAttachment
from .storage import get_private_storage
from .utils import clean_tags, content_fingerprint, make_slug, normalize_text, unique_slug

GENERIC_DIRS = {"guides", "skills", "skill", "docs", "md", "markdown", "src", "library", "content", "packs", "export", "exports", "attachments"}
IGNORED_DIRS = {"__macosx", ".git", ".svn", ".hg", "node_modules", "__pycache__", ".idea", ".vscode"}
IGNORED_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
ATTACHMENT_EXTS = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xlsx", ".docx", ".pptx", ".sql"}
STAGE_RE = re.compile(r"^[0-9a-f]{32}$")
ID_PREFIX = re.compile(r"^([A-Za-z]{1,6}-\d{1,4})\s*[-–—:]\s*(.+)$")


class UnsafePath(ValueError):
    pass


# ----------------------------------------------------------------------------- path safety
def clean_relpath(name):
    """Normalise an archive/upload path or raise UnsafePath. Never touches the filesystem."""
    if not isinstance(name, str) or not name:
        raise UnsafePath("empty name")
    if any(ord(c) < 32 for c in name):
        raise UnsafePath("control characters in name")
    p = name.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        raise UnsafePath("absolute path")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts:
        raise UnsafePath("empty name")
    if ".." in parts:
        raise UnsafePath("path traversal ('..')")
    if any(":" in x for x in parts):
        raise UnsafePath("':' is not allowed in names")
    if len(p) > 240 or any(len(x) > 120 for x in parts):
        raise UnsafePath("name too long")
    return "/".join(parts)


def _is_metadata(path):
    parts = [x.lower() for x in path.split("/")]
    return any(x in IGNORED_DIRS for x in parts[:-1]) or parts[-1] in IGNORED_FILES or parts[-1].startswith(".")


# ----------------------------------------------------------------------------- reading uploads
class Report:
    def __init__(self):
        self.rejected = []  # (path, reason) - unsafe or invalid, never imported
        self.ignored = []  # (path, reason) - harmless files that are not skills
        self.fatal = []  # (upload name, reason) - whole upload unusable

    def reject(self, path, reason):
        self.rejected.append((path, reason))

    def ignore(self, path, reason):
        self.ignored.append((path, reason))


def read_zip(upload_name, data, report):
    """Return [(path, bytes)] for acceptable entries, applying every safety limit."""
    out = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        report.fatal.append((upload_name, "not a valid ZIP archive"))
        return out
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > settings.IMPORT_MAX_FILES:
        report.fatal.append((upload_name, f"too many files ({len(infos)}; limit {settings.IMPORT_MAX_FILES})"))
        return out
    declared = sum(i.file_size for i in infos)
    if declared > settings.IMPORT_MAX_UNCOMPRESSED_BYTES:
        report.fatal.append((upload_name, f"uncompressed size {declared:,} bytes exceeds the limit"))
        return out
    total = 0
    for info in infos:
        label = f"{upload_name}:{info.filename}"
        try:
            path = clean_relpath(info.filename)
        except UnsafePath as exc:
            report.reject(label, f"unsafe path - {exc}")
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            report.reject(label, "symbolic links are not allowed")
            continue
        if info.flag_bits & 0x1:
            report.reject(label, "encrypted entries are not supported")
            continue
        if _is_metadata(path):
            report.ignore(label, "system/metadata file")
            continue
        if path.lower().endswith((".zip", ".7z", ".rar", ".gz", ".tar")):
            report.ignore(label, "nested archives are not unpacked")
            continue
        if info.file_size > settings.IMPORT_MAX_ENTRY_BYTES:
            report.reject(label, "file is larger than the per-file limit")
            continue
        if info.compress_size and info.file_size > 1_000_000 and info.file_size / info.compress_size > settings.IMPORT_MAX_COMPRESSION_RATIO:
            report.reject(label, "suspicious compression ratio")
            continue
        with zf.open(info) as fh:  # never trust the declared size: cap the real read
            blob = fh.read(settings.IMPORT_MAX_ENTRY_BYTES + 1)
        if len(blob) > settings.IMPORT_MAX_ENTRY_BYTES:
            report.reject(label, "file is larger than the per-file limit")
            continue
        total += len(blob)
        if total > settings.IMPORT_MAX_UNCOMPRESSED_BYTES:
            report.fatal.append((upload_name, "decompressed size limit exceeded; archive stopped"))
            break
        out.append((path, blob))
    return out


def collect_raw_files(uploads, report, allow_html=False):
    """uploads: iterable of (filename, bytes). Returns [(path, bytes)] from loose files and ZIPs."""
    raw = []
    for name, data in uploads:
        try:
            base = clean_relpath(name).split("/")[-1]
        except UnsafePath as exc:
            report.fatal.append((str(name)[:80], f"unsafe file name - {exc}"))
            continue
        if len(data) > settings.IMPORT_MAX_UPLOAD_BYTES:
            report.fatal.append((base, "file exceeds the upload size limit"))
            continue
        if base.lower().endswith(".zip"):
            raw.extend(read_zip(base, data, report))
        elif _is_metadata(base):
            report.ignore(base, "system/metadata file")
        elif base.lower().endswith((".md", ".json", ".txt") + ((".html",) if allow_html else ())):
            if len(data) > settings.IMPORT_MAX_ENTRY_BYTES:
                report.fatal.append((base, "file exceeds the per-file size limit"))
            else:
                raw.append((base, data))
        else:
            report.fatal.append((base, "unsupported file type (use .md, .zip or .json)"))
    return raw


# ----------------------------------------------------------------------------- parsing
def _decode(blob):
    if b"\x00" in blob:
        raise ValueError("binary content")
    try:
        return blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("not valid UTF-8 text")


def _parse_front_matter(text):
    m = FRONT_MATTER.match(text)
    if not m:
        return {}
    data, current = {}, None
    for line in m.group(0).splitlines()[1:-1]:
        if re.match(r"^\s*-\s+", line) and current:
            data.setdefault(current, [])
            if isinstance(data[current], list):
                data[current].append(re.sub(r"^\s*-\s+", "", line).strip().strip("\"'"))
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current = key.strip().lower()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                data[current] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
            elif value:
                data[current] = value.strip("\"'")
            else:
                data[current] = []
    return data


META_RE = re.compile(r"^[-*\s]*\**(Domain|Subdomain|Title|ID|Level|Summary)\**\s*:\s*\**\s*(.+?)\s*$", re.IGNORECASE)


def _scan_meta(text):
    meta = {}
    for i, line in enumerate(text.split("\n")[:70]):
        m = META_RE.match(line)
        if m:
            key = m.group(1).lower()
            if key in ("summary", "level") or i < 40:
                meta.setdefault(key, m.group(2).strip("* "))
    return meta


def _humanise(dirname):
    name = re.sub(r"[-_]+", " ", dirname).strip()
    return name.title() if name == name.lower() else name


def _first_paragraph(text):
    body = FRONT_MATTER.sub("", text, count=1)
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if block and not re.match(r"^([#>|`~\-*\[]|\d+\.)", block):
            return re.sub(r"\s+", " ", block)
    return ""


def parse_skill_text(text, path, package_dir=None):
    """Build a candidate dict from skill text. `path` is the archive/upload path of the main file."""
    text = normalize_text(text)
    fm = _parse_front_matter(text)
    meta = _scan_meta(text)
    parts = path.split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    heading = next((m.group(1).strip() for m in re.finditer(r"^#\s+(.+?)\s*#*$", "\n".join(text.split("\n")[:30]), re.M)), "")
    title = str(fm.get("title") or heading or meta.get("title") or "")
    source_ref = meta.get("id", "")
    if not title:
        title = _humanise(package_dir.split("/")[-1] if package_dir else stem)
    m = ID_PREFIX.match(title)
    if m:
        source_ref, title = source_ref or m.group(1).upper(), m.group(2).strip()
    description = str(fm.get("description") or meta.get("summary") or _first_paragraph(text))[:500]

    # Category: explicit front matter, then folder structure, then the guide's own Domain line.
    folder_parts = (package_dir.split("/")[:-1] if package_dir else parts[:-1])
    folders = [f for f in folder_parts if f.lower() not in GENERIC_DIRS]
    domain = meta.get("domain", "")
    domain_main, _, domain_sub = domain.partition(" / ")
    sub_name = str(fm.get("subcategory") or "")
    if fm.get("category"):
        category = str(fm["category"])
    elif len(folders) >= 2:
        category, sub_name = _humanise(folders[-2]), sub_name or _humanise(folders[-1])
    elif folders:
        category = _humanise(folders[-1])
    else:
        category = domain_main.strip() or "Uncategorized"
    if not sub_name and not len(folders) >= 2:
        sub_name = (meta.get("subdomain") or domain_sub).strip()

    tags = fm.get("tags")
    if not tags:
        tags = [t for t in (meta.get("subdomain") or domain_sub.strip(), meta.get("level")) if t]
    return {
        "title": title[:200],
        "slug": make_slug(str(fm.get("slug") or title)),
        "description": description,
        "tags": clean_tags(tags),
        "category": category[:120],
        "subcategory": sub_name[:120],
        "content": text,
        "original_filename": parts[-1],
        "source_ref": source_ref[:40],
        "source": path,
        "attachments": [],
        "errors": [],
    }


def parse_json_library(blob, path, raw_by_path):
    data = json.loads(_decode(blob))
    items = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) and "title" in i for i in items) \
            or not any("content" in i for i in items):
        return None  # e.g. a pack MANIFEST.json (metadata only) - not a library export
    cands = []
    for n, item in enumerate(items, 1):
        label = f"{path}#{n}"
        content, title = item.get("content"), item.get("title")
        cat = item.get("category")
        cat = cat.get("name") if isinstance(cat, dict) else cat
        cand = {
            "title": str(title or "")[:200], "slug": make_slug(str(item.get("slug") or title or "")),
            "description": str(item.get("description") or "")[:500], "tags": clean_tags(item.get("tags") or ""),
            "category": str(cat or "Uncategorized")[:120], "subcategory": str(item.get("subcategory") or "")[:120],
            "content": normalize_text(content) if isinstance(content, str) else "",
            "original_filename": str(item.get("original_filename") or "")[:255], "source_ref": str(item.get("source_ref") or "")[:40],
            "source": label, "attachments": [], "errors": [],
        }
        if not isinstance(content, str) or not content.strip():
            cand["errors"].append("missing content")
        if not cand["title"].strip():
            cand["errors"].append("missing title")
        for att in item.get("attachments") or []:
            try:
                rel = clean_relpath(att["path"])
                blob_path = clean_relpath(att["file"])
            except (KeyError, UnsafePath, TypeError):
                cand["errors"].append("unsafe attachment entry skipped")
                continue
            if blob_path in raw_by_path:
                cand["attachments"].append({"path": rel, "data": raw_by_path[blob_path]})
        cands.append(cand)
    return cands


def parse_standalone_html(blob, path):
    """Read the skill data embedded in the original standalone library page (`const STARTER=[...]`).

    The array is plain JSON, so it is decoded with the json module - the page's JavaScript is never run.
    Returns None when the page holds no such data.
    """
    text = _decode(blob)
    start = text.find("const STARTER=")
    if start < 0:
        return None
    items, _ = json.JSONDecoder().raw_decode(text, text.index("[", start))
    stems = {}
    for it in items:
        stems.setdefault(re.sub(r"^(base|collection)-", "", str(it.get("id", ""))), []).append(it)
    titles = {}
    for it in items:
        titles.setdefault(str(it.get("title", "")).strip().lower(), []).append(it)
    candidates = []
    for n, it in enumerate(items, 1):
        sid = str(it.get("id") or f"item-{n}")
        title = str(it.get("title") or "").strip()
        base_slug = re.sub(r"^(base|collection)-", "", sid)
        if sid.startswith("collection-") and (len(stems.get(base_slug, [])) > 1 or len(titles.get(title.lower(), [])) > 1):
            # an expanded version of an earlier "base" skill with different instructions: keep both, clearly labelled
            base_slug += "-expanded"
            if len(titles.get(title.lower(), [])) > 1:
                title = f"{title} (Expanded)"
        tags = list(it.get("tags") or []) + [t for t in (it.get("subcategory"), it.get("level")) if t]
        content = it.get("content")
        cand = {
            "title": title[:200], "slug": make_slug(base_slug), "description": str(it.get("description") or "")[:500],
            "tags": clean_tags(tags), "category": str(it.get("category") or "Uncategorized")[:120], "subcategory": str(it.get("subcategory") or "")[:120],
            "content": normalize_text(content) if isinstance(content, str) else "", "original_filename": "",
            "source_ref": sid[:80], "source": f"{path}#{sid}", "attachments": [], "errors": [],
        }
        if not cand["content"].strip():
            cand["errors"].append("missing content")
        candidates.append(cand)
    return candidates


def build_candidates(raw_files, report):
    """Turn [(path, bytes)] into candidate dicts."""
    raw_by_path = dict(raw_files)
    pkg_dirs = sorted({p.rsplit("/", 1)[0] if "/" in p else "" for p, _ in raw_files if p.split("/")[-1].lower() == "skill.md"}, key=len, reverse=True)

    def package_of(path):
        for d in pkg_dirs:
            if d == "" or path.startswith(d + "/"):
                return d
        return None

    candidates, handled = [], set()
    # A backup ZIP made by this app: library.json is authoritative; its skills/ and attachments/ copies are not re-read.
    is_backup = "library.json" in raw_by_path
    for path, blob in raw_files:
        name, lower = path.split("/")[-1], path.lower()
        if is_backup and lower.startswith(("skills/", "attachments/")):
            report.ignore(path, "covered by library.json in this backup")
            continue
        pkg = package_of(path)
        if pkg is not None and path.split("/")[-1].lower() != "skill.md":
            continue  # attachment, attached below
        try:
            if name.lower() == "skill.md" and pkg is not None:
                cand = parse_skill_text(_decode(blob), path, package_dir=pkg or "")
                if pkg == "":
                    cand["category"] = cand["category"] if cand["category"] != "Uncategorized" else "Uncategorized"
                prefix = pkg + "/" if pkg else ""
                for apath, ablob in raw_files:
                    if apath == path or not apath.startswith(prefix) or package_of(apath) != pkg:
                        continue
                    rel = apath[len(prefix):]
                    if PurePosixPath(rel).suffix.lower() in ATTACHMENT_EXTS:
                        cand["attachments"].append({"path": rel, "data": ablob})
                        handled.add(apath)
                    else:
                        report.reject(apath, "attachment type not allowed")
                candidates.append(cand)
            elif lower.endswith(".md") or (lower.endswith(".txt") and "guides" in [x.lower() for x in path.split("/")[:-1]]):
                candidates.append(parse_skill_text(_decode(blob), path))
            elif lower.endswith(".html"):
                cands = parse_standalone_html(blob, path)
                if cands is None:
                    report.ignore(path, "HTML file holds no skill data")
                else:
                    candidates.extend(cands)
            elif lower.endswith(".json"):
                cands = parse_json_library(blob, path, raw_by_path)
                if cands is None:
                    report.ignore(path, "JSON file is not a skill library export")
                else:
                    candidates.extend(cands)
            else:
                report.ignore(path, "not a skill file")
        except (ValueError, json.JSONDecodeError) as exc:
            report.reject(path, str(exc))
    for cand in candidates:
        if not cand["content"].strip():
            cand["errors"].append("empty content")
        if not cand["title"].strip():
            cand["errors"].append("missing title")
    return candidates


# ----------------------------------------------------------------------------- duplicates
def assess_duplicates(candidates):
    """Attach `dup` info and a default `action` to each candidate (never defaults to overwrite)."""
    by_hash = {s.content_hash: s for s in Skill.objects.exclude(content_hash="")}
    by_slug = {s.slug.lower(): s for s in Skill.objects.all()}
    by_title = {s.title.lower(): s for s in Skill.objects.all()}
    by_ref = {s.source_ref.lower(): s for s in Skill.objects.exclude(source_ref="")}
    seen_hash, seen_slug, seen_title = {}, {}, {}
    cat_slugs = {c.slug for c in Category.objects.all()} | {make_slug(c.name, "category") for c in Category.objects.all()}
    for i, c in enumerate(candidates):
        c["key"] = str(i)
        cname = CATEGORY_ALIASES.get(c["category"].lower(), c["category"])
        c["cat_exists"] = make_slug(cname, "category") in cat_slugs
        c["sub_exists"] = bool(c.get("subcategory")) and Category.objects.filter(parent__name__iexact=cname, name__iexact=c["subcategory"]).exists()
        kinds = []
        if c["content"] and content_fingerprint(c["content"]) in by_hash:
            kinds.append("content")
        if c["slug"].lower() in by_slug:
            kinds.append("slug")
        if c["title"].lower() in by_title:
            kinds.append("title")
        c["kinds"] = kinds
        c["hash"] = content_fingerprint(c["content"]) if c["content"] else ""
        c["dup"] = None
        existing = (by_hash.get(c["hash"]) or by_ref.get(c["source_ref"].lower() if c["source_ref"] else None)
                    or by_slug.get(c["slug"].lower()) or by_title.get(c["title"].lower()))
        if existing:
            if existing.content_hash == c["hash"]:
                kind = "identical content"
            elif c["source_ref"] and existing.source_ref.lower() == c["source_ref"].lower():
                kind = "same source reference"
            else:
                kind = "same slug" if existing.slug.lower() == c["slug"].lower() else "same title"
            c["dup"] = {"kind": kind, "existing_id": existing.pk, "existing_title": existing.title, "existing_status": existing.get_status_display()}
        elif c["hash"] in seen_hash or c["slug"].lower() in seen_slug or c["title"].lower() in seen_title:
            first = seen_hash.get(c["hash"]) or seen_slug.get(c["slug"].lower()) or seen_title.get(c["title"].lower())
            c["kinds"] = [k for k, hit in (("content", c["hash"] in seen_hash), ("slug", c["slug"].lower() in seen_slug), ("title", c["title"].lower() in seen_title)) if hit]
            c["dup"] = {"kind": "duplicate within this upload", "existing_id": None, "existing_title": first, "existing_status": ""}
        c["action"] = "error" if c["errors"] else ("skip" if c["dup"] else "import")
        seen_hash.setdefault(c["hash"], c["title"])
        seen_slug.setdefault(c["slug"].lower(), c["title"])
        seen_title.setdefault(c["title"].lower(), c["title"])
    return candidates


def analyse_uploads(uploads, allow_html=False):
    report = Report()
    candidates = assess_duplicates(build_candidates(collect_raw_files(uploads, report, allow_html), report))
    return candidates, report


# ----------------------------------------------------------------------------- staging (admin UI)
def stage_dir(token=None):
    root = Path(settings.PRIVATE_MEDIA_ROOT) / "imports"
    return root if token is None else root / token


def cleanup_old_stages(max_age_hours=24):
    root = stage_dir()
    if not root.exists():
        return
    for d in root.iterdir():
        if d.is_dir() and time.time() - d.stat().st_mtime > max_age_hours * 3600:
            shutil.rmtree(d, ignore_errors=True)


def stage_import(user, candidates, report):
    cleanup_old_stages()
    token = uuid.uuid4().hex
    d = stage_dir(token)
    (d / "att").mkdir(parents=True)
    slim, n = [], 0
    for c in candidates:
        c = dict(c)
        atts = []
        for a in c["attachments"]:
            n += 1
            (d / "att" / f"{n}.bin").write_bytes(a["data"])
            atts.append({"path": a["path"], "blob": f"{n}.bin", "size": len(a["data"])})
        c["attachments"] = atts
        slim.append(c)
    manifest = {"user_id": user.pk, "candidates": slim, "rejected": report.rejected, "ignored": report.ignored, "fatal": report.fatal}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return token


def load_stage(token, user):
    """Return the manifest dict, or None if the token is invalid/foreign/expired."""
    if not STAGE_RE.match(token or ""):
        return None
    f = stage_dir(token) / "manifest.json"
    if not f.is_file():
        return None
    manifest = json.loads(f.read_text(encoding="utf-8"))
    return manifest if manifest.get("user_id") == user.pk else None


def discard_stage(token):
    if STAGE_RE.match(token or ""):
        shutil.rmtree(stage_dir(token), ignore_errors=True)


# ----------------------------------------------------------------------------- commit
class ImportResult:
    def __init__(self):
        self.created, self.replaced, self.skipped, self.errors = [], [], [], []
        self.categories_created = []

    @property
    def imported(self):
        return len(self.created) + len(self.replaced)


CATEGORY_ALIASES = {"training and education": "Training & Learning"}  # guides use a different name for the same area


def _subcategory_for(domain, name, result):
    name = " ".join((name or "").split())[:120]
    if not name:
        return None
    sub = Category.objects.filter(parent=domain, name__iexact=name).first()
    if sub is None:
        sub = Category.objects.create(name=name, parent=domain, display_order=domain.children.count() + 1)
        result.categories_created.append(f"{domain.name} / {sub.name}")
    return sub


def _category_for(name, result):
    name = (name or "Uncategorized").strip()[:120] or "Uncategorized"
    name = CATEGORY_ALIASES.get(name.lower(), name)
    slug = make_slug(name, "category")
    cat = Category.objects.filter(parent__isnull=True, slug=slug).first() or Category.objects.filter(parent__isnull=True, name__iexact=name).first()
    if cat is None:
        cat = Category.objects.create(name=name, slug=slug, display_order=Category.objects.count() + 1)
        result.categories_created.append(cat.name)
    return cat


def commit_candidates(user, candidates, decisions=None, status=Skill.Status.DRAFT, read_blob=None):
    """Write candidates to the database. One transaction per skill so one failure never undoes others.

    decisions: {key: {"action": import|skip|replace|rename, "category": str, "title": str}}
    read_blob: callable(attachment dict) -> bytes (defaults to the in-memory 'data').
    """
    decisions = decisions or {}
    read_blob = read_blob or (lambda a: a["data"])
    result = ImportResult()
    for c in candidates:
        d = decisions.get(c["key"], {})
        action = d.get("action") or c.get("action", "skip")
        label = c["title"] or c["source"]
        if c["errors"] or action in ("skip", "error"):
            (result.errors if c["errors"] else result.skipped).append((label, "; ".join(c["errors"]) or "skipped"))
            continue
        saved_files = []
        try:
            with transaction.atomic():
                category = _category_for(d.get("category") or c["category"], result)
                title = (d.get("title") or c["title"]).strip()[:200]
                sub_text = d["subcategory"] if "subcategory" in d else c.get("subcategory", "")
                subcategory = _subcategory_for(category, sub_text, result)
                fields = dict(title=title, description=c["description"], tags=c["tags"], category=category, subcategory=subcategory, content=c["content"],
                              original_filename=c["original_filename"], source_ref=c["source_ref"])
                if action == "replace":
                    skill = Skill.objects.select_for_update().get(pk=(c.get("dup") or {}).get("existing_id"))
                    for k, v in fields.items():
                        setattr(skill, k, v)
                    skill.save()
                    for old in list(skill.attachments.all()):
                        old.file.delete(save=False)
                        old.delete()
                    bucket = result.replaced
                elif action in ("import", "rename"):
                    if action == "import" and Skill.objects.filter(slug__iexact=c["slug"]).exists():
                        raise ValueError("slug already exists - choose Replace, Rename or Skip")
                    slug = unique_slug(Skill, c["slug"])
                    skill = Skill(slug=slug, status=status, created_by=user if getattr(user, "pk", None) else None, **fields)
                    skill.save()
                    bucket = result.created
                else:
                    raise ValueError(f"unknown action {action!r}")
                for a in c["attachments"]:
                    att = SkillAttachment(skill=skill, path=a["path"], size=a.get("size") or len(a.get("data", b"")))
                    att.file.save(a["path"].split("/")[-1], ContentFile(read_blob(a)), save=True)
                    saved_files.append(att.file.name)
            bucket.append((skill.title, skill.slug))
            log_activity(user, "import_replace" if action == "replace" else "import", "skill", skill.pk, f"Imported '{skill.title}' from {c['source']}")
        except Exception as exc:  # noqa: BLE001 - report per item, keep going
            for name in saved_files:
                get_private_storage().delete(name)
            result.errors.append((label, str(exc)))
    return result


def backfill_subcategories(candidates, user=None, apply=True):
    """Give already-imported skills the subcategory their source record carries.

    Only skills that are an exact source match (same reference) and have no subcategory yet are touched, and only
    their subcategory is set: their domain, status, text and everything else stay as they are.
    """
    result = ImportResult()
    changed = 0
    for c in candidates:
        dup = c.get("dup") or {}
        if not (dup.get("existing_id") and c.get("subcategory") and c.get("source_ref")):
            continue
        skill = Skill.objects.select_related("category").filter(pk=dup["existing_id"], source_ref__iexact=c["source_ref"], subcategory__isnull=True).first()
        if skill is None:
            continue
        domain_name = CATEGORY_ALIASES.get(c["category"].lower(), c["category"])
        if skill.category.name.lower() != domain_name.lower():
            continue
        changed += 1
        if apply:
            skill.subcategory = _subcategory_for(skill.category, c["subcategory"], result)
            skill.save(update_fields=["subcategory", "updated_at"])
    if apply and changed:
        log_activity(user, "classify", "skill", None, f"Assigned subcategories to {changed} existing skills from the source library")
    return changed, result.categories_created
