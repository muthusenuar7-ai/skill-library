import hashlib
import re
import unicodedata

from django.utils.text import slugify


def normalize_text(text):
    """Line-ending/whitespace normalisation used for storage and fingerprints."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n") + "\n"


def content_fingerprint(text):
    return hashlib.sha256(normalize_text(text).strip().encode("utf-8")).hexdigest()


def make_slug(value, fallback="skill", max_length=80):
    slug = slugify(unicodedata.normalize("NFKD", value or ""))[:max_length].strip("-")
    return slug or fallback


def unique_slug(model, base, exclude_pk=None, max_length=80):
    """Return `base` or `base-2`, `base-3`... so that no other row of `model` uses it."""
    base = make_slug(base, max_length=max_length - 6)
    candidate, n = base, 1
    qs = model.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug__iexact=candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


TAG_RE = re.compile(r"[^\w\s\-+#./&]", re.UNICODE)


def clean_tags(value, limit=10):
    """Accept a comma separated string or list; return a normalised comma separated string."""
    if isinstance(value, str):
        value = value.split(",")
    seen, out = set(), []
    for raw in value or []:
        tag = TAG_RE.sub("", str(raw)).strip()[:40]
        tag = re.sub(r"\s+", " ", tag)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return ", ".join(out[:limit])


def safe_filename(name, fallback="file", max_length=100):
    """A readable file name that is safe on Windows, macOS and Linux and inside ZIPs."""
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip(" .-_")
    name = re.sub(r"\s+", "-", name)
    return (name or fallback)[:max_length]
