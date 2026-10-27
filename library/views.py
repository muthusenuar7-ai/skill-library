"""Public (no login) views. Every query goes through Skill.objects.public()."""
import re
from datetime import timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import exports
from .models import Category, SiteSetting, Skill, SkillAttachment
from .taxonomy import popular_topics, public_counts, taxonomy_tree

PAGE_SIZE = 12
SORTS = {"recent": ("-created_at", "-id"), "az": ("title", "id"), "za": ("-title", "-id")}
SORT_LABELS = [("recent", "Recently Added"), ("az", "Alphabetical A-Z"), ("za", "Alphabetical Z-A")]
LEGACY_SORTS = {"new": "recent", "title": "az"}
KEEP_PARAMS = ("q", "domain", "sub", "tag", "sort", "view", "page")


def _search(qs, query):
    """Every word must appear in title, description, tags, content, reference, domain or subcategory."""
    for term in query.split()[:8]:
        qs = qs.filter(
            Q(title__icontains=term) | Q(description__icontains=term) | Q(tags__icontains=term) | Q(content__icontains=term)
            | Q(source_ref__icontains=term) | Q(category__name__icontains=term) | Q(subcategory__name__icontains=term)
        )
    return qs


def _read_filters(request, preset_domain=None, preset_sub=None):
    """Resolve and validate ?q ?domain ?sub ?tag ?sort ?view from the URL."""
    g = request.GET
    f = {"q": g.get("q", "").strip()[:100], "tag": g.get("tag", "").strip()[:40], "view": "list" if g.get("view") == "list" else "grid"}
    sort = LEGACY_SORTS.get(g.get("sort", ""), g.get("sort", ""))
    f["sort"] = sort if sort in SORTS else "recent"
    domain = preset_domain or Category.objects.filter(parent__isnull=True, is_active=True, slug=g.get("domain", "")).first()
    sub = preset_sub or Category.objects.filter(parent__isnull=False, is_active=True, slug=g.get("sub", "")).select_related("parent").first()
    if sub and (domain is None or sub.parent_id != domain.pk):
        domain = domain if (domain and sub.parent_id == domain.pk) else sub.parent
        if domain is None or not domain.is_active:
            sub = None
    f["domain"], f["sub"] = domain, sub if domain else None
    return f


def _filtered(f):
    qs = Skill.objects.public().select_related("subcategory")
    if f["domain"]:
        qs = qs.filter(category=f["domain"])
    if f["sub"]:
        qs = qs.filter(subcategory=f["sub"])
    if f["tag"]:
        qs = qs.filter(tags__icontains=f["tag"])
    if f["q"]:
        qs = _search(qs, f["q"])
    return qs.order_by(*SORTS[f["sort"]])


def _explore_url(f, **over):
    """URL of /skills/ with the current filters and some replaced; None/'' removes a parameter. Page always resets."""
    params = {"q": f["q"], "domain": f["domain"].slug if f["domain"] else "", "sub": f["sub"].slug if f["sub"] else "", "tag": f["tag"],
              "sort": f["sort"] if f["sort"] != "recent" else "", "view": f["view"] if f["view"] != "grid" else ""}
    for k, v in over.items():
        params[k] = v.slug if isinstance(v, Category) else (v or "")
    if over.get("domain") is not None and "sub" not in over:
        params["sub"] = ""
    params = {k: v for k, v in params.items() if v}
    return reverse("skill_list") + ("?" + urlencode(params) if params else "")


def explore(request, preset_domain=None, preset_sub=None):
    f = _read_filters(request, preset_domain, preset_sub)
    qs = _filtered(f)
    paginator = Paginator(qs, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    tree = taxonomy_tree()
    for d in tree:
        d.url = _explore_url(f, domain=d, sub="")
        d.is_current = bool(f["domain"] and f["domain"].pk == d.pk)
        for s in d.subs:
            s.url = _explore_url(f, domain=d, sub=s)
            s.is_current = bool(f["sub"] and f["sub"].pk == s.pk)
    chips = []
    if f["domain"]:
        chips.append({"label": f["domain"].name, "kind": "Domain", "remove": _explore_url(f, domain="", sub="")})
    if f["sub"]:
        chips.append({"label": f["sub"].name, "kind": "Subcategory", "remove": _explore_url(f, sub="")})
    if f["tag"]:
        chips.append({"label": f["tag"], "kind": "Topic", "remove": _explore_url(f, tag="")})
    if f["q"]:
        chips.append({"label": f["q"], "kind": "Search", "remove": _explore_url(f, q="")})
    back = urlencode({k: v for k, v in request.GET.items() if k in KEEP_PARAMS and v})
    if preset_domain or preset_sub:
        back = urlencode({**{k: v for k, v in request.GET.items() if k in KEEP_PARAMS and v}, "domain": f["domain"].slug, **({"sub": f["sub"].slug} if f["sub"] else {})})
    selection_qs = urlencode({k: v for k, v in (("q", f["q"]), ("domain", f["domain"].slug if f["domain"] else ""), ("sub", f["sub"].slug if f["sub"] else ""), ("tag", f["tag"])) if v})
    heading = f["sub"].name if f["sub"] else f["domain"].name if f["domain"] else "Explore Skills"
    return render(request, "library/skills.html", {
        "page": page, "total": paginator.count, "f": f, "tree": tree, "chips": chips, "clear_url": reverse("skill_list"),
        "sort_options": SORT_LABELS, "heading": heading, "back_qs": back, "selection_qs": selection_qs, "all_total": sum(d.published_total for d in tree),
        "view_urls": {"grid": _explore_url(f, view="grid"), "list": _explore_url(f, view="list")},
    })


def skill_list(request):
    return explore(request)


def category_detail(request, slug):
    cat = get_object_or_404(Category, slug=slug, is_active=True)
    if cat.parent_id:
        return explore(request, preset_domain=cat.parent, preset_sub=cat)
    return explore(request, preset_domain=cat)


def home(request):
    """Landing page. Every number and card comes from published records."""
    public = Skill.objects.public().select_related("subcategory")
    tree = taxonomy_tree()
    by_domain = sorted(tree, key=lambda d: (-d.published_total, d.display_order, d.name))[:8]
    for d in by_domain:
        d.top_subs = sorted(d.subs, key=lambda x: (-x.published_total, x.name))[:3]
        d.blurb = d.description or (", ".join(x.name for x in d.top_subs) if d.top_subs else "")
    month_ago = timezone.now() - timedelta(days=30)
    stats = {
        "skills": sum(d.published_total for d in tree),
        "domains": len(tree),
        "subcategories": sum(len(d.subs) for d in tree),
        "recent": public.filter(created_at__gte=month_ago).count(),
    }
    return render(request, "library/home.html", {
        "stats": stats, "domains": by_domain, "domain_total": len(tree),
        "featured": public.filter(is_featured=True).order_by("-updated_at", "title")[:6],
        "recent": public.order_by("-created_at", "-id")[:6],
        "topics": popular_topics(6),
    })


def categories_overview(request):
    return render(request, "library/categories.html", {"tree": taxonomy_tree()})


def _safe_back(request):
    """Rebuild a back-to-results link from ?back= using only known parameters (never a free-form URL)."""
    raw = request.GET.get("back", "")
    if not raw or len(raw) > 400:
        return None
    from django.http import QueryDict
    qd = QueryDict(raw)
    clean = {k: qd[k][:100] for k in KEEP_PARAMS if qd.get(k)}
    return reverse("skill_list") + ("?" + urlencode(clean) if clean else "")


def skill_detail(request, slug):
    skill = get_object_or_404(Skill.objects.public().select_related("subcategory").prefetch_related("attachments"), slug=slug)
    related = Skill.objects.public().filter(category=skill.category).exclude(pk=skill.pk).order_by("title")[:4]
    return render(request, "library/detail.html", {"skill": skill, "related": related, "back_url": _safe_back(request)})


def favorites(request):
    return render(request, "library/favorites.html")


SLUG_RE = re.compile(r"^[-a-z0-9_]{1,80}$")


def favorites_cards(request):
    """Cards for the visitor's saved slugs (sent by the browser). Unpublished/removed slugs are simply not returned."""
    slugs = [s for s in request.GET.get("slugs", "").split(",") if SLUG_RE.match(s)][:settings.COLLECTION_MAX_SKILLS * 2]
    skills = list(Skill.objects.public().select_related("subcategory").filter(slug__in=slugs).order_by("title"))
    html = render_to_string("library/_cards.html", {"skills": skills, "fav_manage": True}, request=request)
    return JsonResponse({"html": html, "found": [s.slug for s in skills], "ids": {s.slug: s.pk for s in skills}})


def help_page(request):
    return render(request, "library/help.html", {"download_enabled": SiteSetting.load().public_library_download_enabled})


def about_page(request):
    by_domain, by_sub = public_counts()
    return render(request, "library/about.html", {"skill_total": sum(by_domain.values())})


def skill_download(request, slug):
    skill = get_object_or_404(Skill.objects.public(), slug=slug)
    return exports.attachment_response(skill.content, exports.skill_filename(skill), "text/markdown; charset=utf-8")


def skill_package_download(request, slug):
    skill = get_object_or_404(Skill.objects.public().prefetch_related("attachments"), slug=slug)
    return exports.zip_response(exports.skill_package_zip(skill), exports.zip_name(skill.title, "Package"))


def skill_attachment(request, slug, attachment_id):
    skill = get_object_or_404(Skill.objects.public(), slug=slug)
    att = get_object_or_404(SkillAttachment, pk=attachment_id, skill=skill)
    return exports.file_response(att.file, att.path.split("/")[-1])


def download_category(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    skills = Skill.objects.public().filter(subcategory=category) if category.parent_id else Skill.objects.public().filter(category=category)
    if not skills.exists():
        raise Http404("No published skills in this category.")
    return exports.zip_response(exports.skills_zip(skills), exports.zip_name(category.name))


def _library_download_allowed():
    return SiteSetting.load().public_library_download_enabled


def download_all(request):
    if not _library_download_allowed():
        raise Http404("Whole-library download is turned off.")
    skills = Skill.objects.public()
    if not skills.exists():
        raise Http404("No published skills yet.")
    return exports.zip_response(exports.skills_zip(skills), "All-Published-Skills.zip")


def download_selection(request):
    """ZIP of whatever the visitor's current search/domain/subcategory filters show (published skills only)."""
    f = _read_filters(request)
    qs = _filtered(f)
    if not (f["q"] or f["tag"] or f["domain"]) and not _library_download_allowed():
        raise Http404("Whole-library download is turned off.")
    if not qs.exists():
        raise Http404("Nothing to download.")
    return exports.zip_response(exports.skills_zip(qs), "Selected-Skills.zip")


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)


# ----------------------------------------------------------------------------- My Collection (multi-skill selection)
def _parse_ids(values, cap):
    """Unique positive integers from ['1,2', '3'] style input; junk is ignored and the work is bounded."""
    ids, seen = [], set()
    for chunk in values:
        for part in str(chunk).split(",")[:2000]:
            part = part.strip()
            if part.isdigit() and len(part) <= 9:
                n = int(part)
                if n not in seen:
                    seen.add(n)
                    ids.append(n)
    return ids[:cap]


def _public_by_ids(ids):
    """Currently published skills for these ids, in the order they were selected (the client's status is never trusted)."""
    found = {s.pk: s for s in Skill.objects.public().select_related("subcategory").prefetch_related("attachments").filter(pk__in=ids)}
    return [found[i] for i in ids if i in found]


def collection_page(request):
    return render(request, "library/collection.html", {"download_enabled": True})


def collection_items(request):
    cap = settings.COLLECTION_MAX_SKILLS * 2
    ids = _parse_ids(request.GET.getlist("ids"), cap)
    skills = _public_by_ids(ids)
    groups = {}
    for s in skills:
        groups.setdefault(s.category, []).append(s)
    html = render_to_string("library/_collection_items.html", {"groups": sorted(groups.items(), key=lambda kv: (kv[0].display_order, kv[0].name))}, request=request)
    valid = [s.pk for s in skills]
    return JsonResponse({"html": html, "valid": valid, "invalid": [i for i in ids if i not in set(valid)], "limit": settings.COLLECTION_MAX_SKILLS})


def collection_check(request):
    ids = _parse_ids(request.GET.getlist("ids"), settings.COLLECTION_MAX_SKILLS * 2)
    valid = set(Skill.objects.public().filter(pk__in=ids).values_list("pk", flat=True))
    return JsonResponse({"valid": [i for i in ids if i in valid]})


def collection_match(request):
    """How many published skills match the visitor's filters, plus at most `limit` of their ids (never the whole library)."""
    f = _read_filters(request)
    qs = _filtered(f)
    limit = settings.COLLECTION_MAX_SKILLS
    return JsonResponse({"count": qs.count(), "ids": list(qs.values_list("pk", flat=True)[:limit]), "limit": limit})


def _zip_download(request, skills, filename, empty_message, back):
    from .exports import SelectionTooLarge, selection_zip, zip_file_response
    if not skills:
        messages.error(request, empty_message)
        return redirect(back, permanent=False)
    try:
        fileobj = selection_zip(skills)
    except SelectionTooLarge as exc:
        messages.error(request, str(exc))
        return redirect(back)
    resp = zip_file_response(fileobj, filename)
    with_support = sum(1 for s in skills if s.attachments.all())
    if with_support:  # the ZIP holds Markdown only; some of these skills also have a separate full package
        resp["X-Skills-With-Supporting-Files"] = str(with_support)
    return resp


@require_POST
def collection_download(request):
    limit = settings.COLLECTION_MAX_SKILLS
    # Download requests are all-or-nothing. The forgiving display parser can
    # discard malformed tokens and truncate input; it must never choose a ZIP's contents.
    ids, seen = [], set()
    for chunk in request.POST.getlist("ids"):
        for part in chunk.split(","):
            part = part.strip()
            if not re.fullmatch(r"[0-9]{1,19}", part) or not 0 < int(part) <= 9223372036854775807:
                messages.error(request, "Invalid selected skill IDs. No ZIP was downloaded. Review your collection and try again.")
                return redirect("collection")
            pk = int(part)
            if pk not in seen:
                seen.add(pk)
                ids.append(pk)
            if len(ids) > limit:
                messages.error(request, f"My Collection can hold up to {limit} skills. Remove some and try again.")
                return redirect("collection")
    skills = _public_by_ids(ids)
    missing = len(set(ids) - {s.pk for s in skills})
    if missing:
        label = "skill is" if missing == 1 else "skills are"
        messages.error(request, f"{missing} selected {label} no longer available. No ZIP was downloaded. Review your collection and select the available skills again.")
        return redirect("collection")
    resp = _zip_download(request, skills, "My-Selected-Skills.zip",
                         "Nothing to download: your collection is empty or its skills are no longer available.", "collection")
    return resp


@require_POST
def favorites_download(request):
    limit = settings.COLLECTION_MAX_SKILLS
    slugs = [x for x in ",".join(request.POST.getlist("slugs")).split(",") if SLUG_RE.match(x)]
    slugs = list(dict.fromkeys(slugs))
    if len(slugs) > limit:
        messages.error(request, f"A favorites download can include up to {limit} skills. Remove some favorites and try again.")
        return redirect("favorites")
    skills = list(Skill.objects.public().select_related("subcategory").prefetch_related("attachments").filter(slug__in=slugs).order_by("category__name", "title"))
    return _zip_download(request, skills, "My-Favorites.zip", "Nothing to download: none of your favorites are available any more.", "favorites")
