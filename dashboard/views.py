"""Management interface. One dashboard for every role; each view is gated server-side by permissions."""
import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from accounts.decorators import admin_permission_required as perm
from accounts.forms import TeamCreateForm, TeamEditForm
from accounts.models import ROLE_LABELS, ROLE_SUPER, User
from accounts.roles import set_role
from library import activity_service, exports, importer
from library.activity_rules import action_label
from library.markdown_utils import render_markdown
from library.activity import log_activity
from library.models import ActivityLog, Category, SecurityAuditLog, SiteSetting, Skill, SkillAttachment

from .forms import CategoryForm, SettingsForm, SkillForm

S = Skill.Status


# ----------------------------------------------------------------------------- dashboard
@perm("library.view_skill")
def dashboard(request):
    ctx = {
        "total_skills": Skill.objects.count(),
        "published": Skill.objects.filter(status=S.PUBLISHED).count(),
        "drafts": Skill.objects.filter(status=S.DRAFT).count(),
        "archived": Skill.objects.filter(status=S.ARCHIVED).count(),
        "total_categories": Category.objects.count(),
        "recent": Skill.objects.select_related("category", "subcategory").order_by("-updated_at")[:6],
        "activity": _activity_queryset(request.user)[:8],
    }
    if request.user.has_perm("accounts.manage_team"):
        ctx["team_members"] = User.objects.filter(groups__name__in=ROLE_LABELS.values()).distinct().order_by("email")
    return render(request, "dashboard/index.html", ctx)


# ----------------------------------------------------------------------------- skills
@perm("library.view_skill")
def manage_skills(request):
    qs = Skill.objects.select_related("category", "subcategory")
    q = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "")
    cat = request.GET.get("category", "")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q) | Q(tags__icontains=q) | Q(source_ref__icontains=q) | Q(subcategory__name__icontains=q))
    if status in S.values:
        qs = qs.filter(status=status)
    if cat.isdigit():
        qs = qs.filter(category_id=int(cat))
    page = Paginator(qs.order_by("-updated_at"), 25).get_page(request.GET.get("page"))
    return render(request, "dashboard/skills.html", {
        "page": page, "q": q, "status": status, "cat": cat, "statuses": S.choices,
        "categories": Category.objects.filter(parent__isnull=True).order_by("display_order", "name"),
    })


def _save_skill(request, instance=None):
    form = SkillForm(request.POST or None, request.FILES or None, instance=instance, user=request.user)
    if request.method == "POST" and form.is_valid():
        can_publish = request.user.has_perm("library.publish_skill")
        skill = form.save(commit=False)
        if not can_publish:  # never trust the posted status/featured fields from accounts without the permission
            skill.status = instance.status if instance else S.DRAFT
            skill.is_featured = instance.is_featured if instance else False
        if instance is None:
            skill.created_by = request.user
        skill.save()
        log_activity(request.user, "skill_update" if instance else "skill_create", "skill", skill.pk, f"{'Edited' if instance else 'Created'} '{skill.title}' ({skill.get_status_display()})")
        messages.success(request, f"Skill '{skill.title}' saved.")
        if "save_preview" in request.POST:
            return None, redirect("skill_preview", pk=skill.pk)
        return None, redirect("manage_skills")
    return form, None


@perm("library.add_skill")
def skill_create(request):
    form, response = _save_skill(request)
    return response or render(request, "dashboard/skill_form.html", {"form": form, "skill": None})


@perm("library.add_skill")
@require_POST
def skill_upload(request):
    """Option B: read an uploaded .md file and open the normal form pre-filled so metadata can be reviewed before saving."""
    f = request.FILES.get("file")
    if f is None or not f.name.lower().endswith(".md"):
        messages.error(request, "Choose a Markdown (.md) file.")
        return redirect("skill_create")
    if f.size > settings.IMPORT_MAX_ENTRY_BYTES:
        messages.error(request, "That file is larger than the per-file limit.")
        return redirect("skill_create")
    try:
        name = importer.clean_relpath(f.name).split("/")[-1]
        text = importer._decode(f.read())
    except (importer.UnsafePath, ValueError) as exc:
        messages.error(request, f"Could not read that file: {exc}.")
        return redirect("skill_create")
    cand = importer.parse_skill_text(text, name)
    existing_cat = Category.objects.filter(name__iexact=cand["category"]).first()
    initial = {
        "title": cand["title"], "slug": cand["slug"], "description": cand["description"], "tags": cand["tags"], "content": cand["content"],
        "status": S.DRAFT, "source_filename": name, "category": existing_cat.pk if existing_cat else None,
        "new_category": "" if existing_cat or cand["category"] == "Uncategorized" else cand["category"],
    }
    clash = Skill.objects.filter(Q(slug__iexact=cand["slug"]) | Q(title__iexact=cand["title"]) | Q(content_hash=importer.content_fingerprint(cand["content"]))).first()
    form = SkillForm(initial=initial, user=request.user)
    return render(request, "dashboard/skill_form.html", {"form": form, "skill": None, "from_upload": name, "clash": clash})


@perm("library.view_skill")
@require_POST
def markdown_preview(request):
    """Sanitised HTML for the editor's Preview tab (same renderer as the public pages)."""
    return JsonResponse({"html": render_markdown(request.POST.get("content", "")[:500_000])})


@perm("library.change_skill")
def skill_edit(request, pk):
    skill = get_object_or_404(Skill.objects.prefetch_related("attachments"), pk=pk)
    form, response = _save_skill(request, skill)
    return response or render(request, "dashboard/skill_form.html", {"form": form, "skill": skill})


@perm("library.view_skill")
def skill_preview(request, pk):
    skill = get_object_or_404(Skill.objects.select_related("category").prefetch_related("attachments"), pk=pk)
    return render(request, "dashboard/skill_preview.html", {"skill": skill})


STATUS_ACTIONS = {
    "publish": (S.PUBLISHED, "published"),
    "unpublish": (S.DRAFT, "unpublished (now a draft)"),
    "archive": (S.ARCHIVED, "archived"),
    "restore": (S.DRAFT, "restored to draft"),
}


def _safe_next(request):
    nxt = request.POST.get("next", "")
    return nxt if nxt.startswith("/admin/") and not nxt.startswith("//") else None


@perm("library.publish_skill")
@require_POST
def skill_status(request, pk):
    skill = get_object_or_404(Skill, pk=pk)
    action = request.POST.get("action", "")
    if action not in STATUS_ACTIONS:
        raise Http404
    skill.status, verb = STATUS_ACTIONS[action]
    skill.save(update_fields=["status", "updated_at"])
    log_activity(request.user, f"skill_{action}", "skill", skill.pk, f"'{skill.title}' {verb}")
    messages.success(request, f"'{skill.title}' {verb}.")
    return redirect(_safe_next(request) or "manage_skills")


@perm("library.publish_skill")
@require_POST
def skill_bulk(request):
    action = request.POST.get("action", "")
    if action not in STATUS_ACTIONS and action not in ("feature", "unfeature"):
        messages.error(request, "Choose an action first.")
        return redirect(_safe_next(request) or "manage_skills")
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()][:500]
    if not ids:
        messages.warning(request, "Tick at least one skill first.")
        return redirect(_safe_next(request) or "manage_skills")
    if action in ("feature", "unfeature"):
        flag = action == "feature"
        n = Skill.objects.filter(pk__in=ids).update(is_featured=flag)
        log_activity(request.user, f"skill_bulk_{action}", "skill", None, f"{n} skills {'added to' if flag else 'removed from'} Featured")
        messages.success(request, f"{n} skill{'s' if n != 1 else ''} {'added to' if flag else 'removed from'} Featured on the home page.")
        return redirect(_safe_next(request) or "manage_skills")
    new_status, verb = STATUS_ACTIONS[action]
    skills = list(Skill.objects.filter(pk__in=ids))
    with transaction.atomic():
        for s in skills:
            s.status = new_status
            s.save(update_fields=["status", "updated_at"])
    log_activity(request.user, f"skill_bulk_{action}", "skill", None, f"{len(skills)} skills {verb}: " + ", ".join(s.title for s in skills[:5]) + ("..." if len(skills) > 5 else ""))
    messages.success(request, f"{len(skills)} skill{'s' if len(skills) != 1 else ''} {verb}.")
    return redirect(_safe_next(request) or "manage_skills")


@perm("library.view_skill")
def skill_download_admin(request, pk):
    skill = get_object_or_404(Skill, pk=pk)
    return exports.attachment_response(skill.content, exports.skill_filename(skill), "text/markdown; charset=utf-8")


@perm("library.view_skill")
def skill_original_file(request, pk):
    skill = get_object_or_404(Skill, pk=pk)
    if not skill.uploaded_file:
        raise Http404
    return exports.file_response(skill.uploaded_file, skill.original_filename or "original")


@perm("library.view_skill")
def attachment_admin(request, pk, att_id):
    att = get_object_or_404(SkillAttachment, pk=att_id, skill_id=pk)
    return exports.file_response(att.file, att.path.split("/")[-1])


# ----------------------------------------------------------------------------- import
@perm("library.import_skills")
def import_skills(request):
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "Choose at least one .md, .zip or .json file.")
            return redirect("import_skills")
        if len(files) > 50:
            messages.error(request, "Upload at most 50 files at a time (put more inside a ZIP).")
            return redirect("import_skills")
        uploads = []
        for f in files:
            if f.size > settings.IMPORT_MAX_UPLOAD_BYTES:
                messages.error(request, f"{f.name} is larger than the upload limit.")
                return redirect("import_skills")
            uploads.append((f.name, f.read()))
        candidates, report = importer.analyse_uploads(uploads)
        if not candidates and not report.rejected and not report.fatal:
            messages.warning(request, "No skills were found in the uploaded files.")
            return redirect("import_skills")
        token = importer.stage_import(request.user, candidates, report)
        return redirect("import_review", token=token)
    return render(request, "dashboard/import.html", {
        "max_upload_mb": settings.IMPORT_MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_files": settings.IMPORT_MAX_FILES,
        "max_unzipped_mb": settings.IMPORT_MAX_UNCOMPRESSED_BYTES // (1024 * 1024),
    })


@perm("library.import_skills")
def import_review(request, token):
    manifest = importer.load_stage(token, request.user)
    if manifest is None:
        messages.error(request, "That import session has expired or does not belong to you. Please upload again.")
        return redirect("import_skills")
    candidates = manifest["candidates"]
    stage = importer.stage_dir(token)

    if request.method == "POST":
        if request.POST.get("cancel"):
            importer.discard_stage(token)
            messages.info(request, "Import cancelled. Nothing was saved.")
            return redirect("import_skills")
        can_create_category = request.user.has_perm("library.add_category")
        decisions = {}
        for c in candidates:
            k = c["key"]
            action = request.POST.get(f"action_{k}", "skip")
            if action not in ("import", "skip", "replace", "rename"):
                action = "skip"
            if action == "replace" and not (c.get("dup") or {}).get("existing_id"):
                action = "skip"
            category = request.POST.get(f"category_{k}", "").strip()
            if category and not can_create_category and not Category.objects.filter(name__iexact=category).exists():
                category = ""  # accounts without add_category may only use existing categories
            sub = " ".join(request.POST.get(f"subcategory_{k}", "").split())[:120]
            if sub and not can_create_category:
                existing = Category.objects.filter(parent__name__iexact=category, name__iexact=sub).exists() if category else False
                if not existing:
                    sub = ""
            decisions[k] = {"action": action, "category": category, "subcategory": sub, "title": request.POST.get(f"title_{k}", "").strip()}
        status = S.PUBLISHED if request.POST.get("status") == "published" and request.user.has_perm("library.publish_skill") else S.DRAFT

        def read_blob(att):
            return (stage / "att" / att["blob"]).read_bytes()

        result = importer.commit_candidates(request.user, candidates, decisions, status, read_blob)
        importer.discard_stage(token)
        return render(request, "dashboard/import_result.html", {"result": result, "status": status, "rejected": manifest["rejected"], "fatal": manifest["fatal"]})

    return render(request, "dashboard/import_review.html", {
        "token": token, "candidates": candidates, "rejected": manifest["rejected"], "ignored": manifest["ignored"], "fatal": manifest["fatal"],
        "categories": Category.objects.filter(parent__isnull=True).order_by("name"),
        "subcategories": Category.objects.filter(parent__isnull=False).select_related("parent").order_by("parent__name", "name"),
        "ready": sum(1 for c in candidates if c["action"] == "import"),
        "dups": sum(1 for c in candidates if c.get("dup")),
        "bad": sum(1 for c in candidates if c["errors"]),
        "bad_total": sum(1 for c in candidates if c["errors"]) + len(manifest["rejected"]),
        "new_cats": sorted({c["category"] for c in candidates if not c["errors"] and not c.get("cat_exists")}),
        "new_subs": sorted({f'{c["category"]} / {c["subcategory"]}' for c in candidates if not c["errors"] and c.get("subcategory") and not c.get("sub_exists")}),
        "support_total": sum(len(c["attachments"]) for c in candidates),
    })


# ----------------------------------------------------------------------------- categories
@perm("library.view_category")
def categories(request):
    form = None
    if request.method == "POST":
        if not request.user.has_perm("library.add_category"):
            raise PermissionDenied
        form = CategoryForm(request.POST)
        if form.is_valid():
            cat = form.save()
            log_activity(request.user, "category_create", "category", cat.pk, f"Created {'subcategory' if cat.parent_id else 'domain'} '{cat}'")
            messages.success(request, f"'{cat}' created.")
            return redirect("manage_categories")
    live = Q(status=S.PUBLISHED)
    domains = Category.objects.filter(parent__isnull=True).annotate(
        total=Count("skills", distinct=True), live=Count("skills", filter=Q(skills__status=S.PUBLISHED), distinct=True),
    ).prefetch_related(Prefetch("children", queryset=Category.objects.annotate(
        total=Count("subcategory_skills", distinct=True), live=Count("subcategory_skills", filter=Q(subcategory_skills__status=S.PUBLISHED), distinct=True),
    ).order_by("display_order", "name")))
    return render(request, "dashboard/categories.html", {"categories": domains, "form": form or CategoryForm()})


@perm("library.change_category")
def category_edit(request, pk):
    cat = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=cat)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_activity(request.user, "category_update", "category", cat.pk, f"Updated category '{cat}'")
        messages.success(request, f"'{cat}' saved.")
        return redirect("manage_categories")
    return render(request, "dashboard/category_form.html", {"form": form, "category": cat})


# ----------------------------------------------------------------------------- team management (Super Admin only)
def _active_super_count(exclude_pk=None):
    qs = User.objects.filter(is_active=True, groups__name=ROLE_LABELS[ROLE_SUPER])
    return qs.exclude(pk=exclude_pk).count() if exclude_pk else qs.count()


def _activation_link(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    path = f"/admin/activate/{uid}/{default_token_generator.make_token(user)}/"
    return (settings.SITE_URL + path) if settings.SITE_URL else request.build_absolute_uri(path)


def _invite(request, user, reset=False):
    """E-mail a single-use setup link when SMTP is configured; always return the link as the fallback."""
    link = _activation_link(request, user)
    sent = False
    if settings.EMAIL_CONFIGURED:
        what = "reset your password" if reset else "activate your account"
        try:
            send_mail(
                "Datacense AI Skill Library - " + ("password reset" if reset else "your account invitation"),
                f"Hello {user.display_name},\n\nUse this one-time link to {what}. It stops working once used or after a few days:\n\n{link}\n\n"
                "If you did not expect this e-mail, ignore it.",
                settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
            )
            sent = True
        except Exception:  # noqa: BLE001 - fall back to showing the link
            messages.warning(request, "The e-mail could not be sent. Use the setup link below instead.")
    return sent, link


def _team_context(request, **extra):
    members = User.objects.filter(Q(groups__name__in=ROLE_LABELS.values()) | Q(is_active=False)).distinct().order_by("email")
    recent = ActivityLog.objects.filter(Q(action__startswith="user_") | Q(action="settings_update"))[:10]
    return {"members": members, "recent_actions": recent, "email_configured": settings.EMAIL_CONFIGURED, **extra}


@perm("accounts.manage_team")
def team(request):
    form = TeamCreateForm(request.POST or None)
    setup = None
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = User(email=form.cleaned_data["email"], full_name=form.cleaned_data["full_name"])
            user.set_unusable_password()
            user.save()
            set_role(user, form.cleaned_data["role"])
        log_activity(request.user, "user_create", "user", user.pk, f"Created account {user.email} as {user.role_label}")
        sent, link = _invite(request, user)
        setup = {"user": user, "link": link, "sent": sent}
        form = TeamCreateForm()
    return render(request, "dashboard/team.html", _team_context(request, form=form, setup=setup))


@perm("accounts.manage_team")
def team_edit(request, pk):
    member = get_object_or_404(User, pk=pk)
    form = TeamEditForm(request.POST or None, member=member, initial={"full_name": member.full_name, "email": member.email, "role": member.role or "admin"})
    if request.method == "POST" and form.is_valid():
        role = form.cleaned_data["role"]
        if role != member.role and member.pk == request.user.pk:
            form.add_error(None, "You cannot change your own role. Ask another Super Admin.")
        elif member.role == ROLE_SUPER and role != ROLE_SUPER and member.is_active and _active_super_count(member.pk) == 0:
            form.add_error(None, "At least one active Super Admin must remain.")
        else:
            old_role = member.role_label
            member.full_name, member.email = form.cleaned_data["full_name"], form.cleaned_data["email"]
            member.save(update_fields=["full_name", "email"])
            set_role(member, role)
            if member.role_label != old_role:
                log_activity(request.user, "user_role_change", "user", member.pk, f"{member.email}: {old_role} -> {member.role_label}")
            log_activity(request.user, "user_update", "user", member.pk, f"Updated details of {member.email}")
            messages.success(request, f"{member.email} updated.")
            return redirect("team")
    return render(request, "dashboard/team_edit.html", {"form": form, "member": member})


@perm("accounts.manage_team")
@require_POST
def team_toggle(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member.is_active:
        if member.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
        elif member.role == ROLE_SUPER and _active_super_count(member.pk) == 0:
            messages.error(request, "At least one active Super Admin must remain.")
        else:
            member.is_active = False
            member.save(update_fields=["is_active"])
            log_activity(request.user, "user_deactivate", "user", member.pk, f"Deactivated {member.email}")
            messages.success(request, f"{member.email} deactivated. They can no longer sign in.")
    else:
        member.is_active = True
        member.save(update_fields=["is_active"])
        log_activity(request.user, "user_reactivate", "user", member.pk, f"Reactivated {member.email}")
        messages.success(request, f"{member.email} reactivated.")
    return redirect("team")


@perm("accounts.manage_team")
@require_POST
def team_link(request, pk):
    member = get_object_or_404(User, pk=pk, is_active=True)
    reset = member.has_usable_password()
    sent, link = _invite(request, member, reset=reset)
    log_activity(request.user, "user_link", "user", member.pk, f"{'Password reset' if reset else 'Invitation'} issued for {member.email}" + (" (e-mailed)" if sent else ""))
    return render(request, "dashboard/team.html", _team_context(request, form=TeamCreateForm(), setup={"user": member, "link": link, "sent": sent, "reset": reset}))


# ----------------------------------------------------------------------------- activity, settings, backup
def _activity_queryset(user, include_cleared=False):
    qs = ActivityLog.objects.select_related("actor")
    if not (include_cleared and activity_service.can_clear(user)):  # only a Super Admin can ever look at cleared (archived) records
        qs = qs.filter(archived_at__isnull=True)
    if user.has_perm("accounts.manage_team"):  # Super Admin sees everything
        return qs
    return qs.filter(Q(target_type__in=["skill", "category"]) | Q(actor=user))  # Admin: content activity + own sign-ins


def _parse_day(value, end=False):
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if end:
        day += datetime.timedelta(days=1)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def _related(entries):
    """Attach the skill / category / account an entry is about (one query per kind, not per row)."""
    wanted = {}
    for e in entries:
        if e.target_id and e.target_type in ("skill", "category", "user"):
            wanted.setdefault(e.target_type, set()).add(e.target_id)
    names = {}
    if "skill" in wanted:
        names["skill"] = {s.pk: (s.title, reverse("skill_edit", args=[s.pk])) for s in Skill.objects.filter(pk__in=wanted["skill"]).only("title")}
    if "category" in wanted:
        names["category"] = {c.pk: (str(c), reverse("category_edit", args=[c.pk])) for c in Category.objects.filter(pk__in=wanted["category"]).select_related("parent")}
    if "user" in wanted:
        names["user"] = {u.pk: (u.email, "") for u in User.objects.filter(pk__in=wanted["user"]).only("email")}
    for e in entries:
        e.label = action_label(e.action)
        e.related_kind = e.target_type
        found = names.get(e.target_type, {}).get(e.target_id) if e.target_id else None
        e.related = found[0] if found else ("(removed)" if e.target_id and e.target_type in names else "")
        e.related_url = found[1] if found else ""
        e.admin_name = (e.actor.full_name if e.actor_id and e.actor.full_name else "") or ("System" if e.actor_email == "system" else "")
    return entries


@perm("library.view_activitylog")
def activity(request):
    user = request.user
    show_cleared = request.GET.get("cleared") == "1" and activity_service.can_clear(user)
    base = _activity_queryset(user, include_cleared=show_cleared)
    qs = base
    f = {k: request.GET.get(k, "").strip()[:100] for k in ("q", "admin", "action", "date_from", "date_to")}
    if f["q"]:
        qs = qs.filter(Q(description__icontains=f["q"]) | Q(actor_email__icontains=f["q"]) | Q(actor__full_name__icontains=f["q"]) | Q(action__icontains=f["q"]))
    if f["admin"]:
        qs = qs.filter(actor_email__iexact=f["admin"])
    if f["action"]:
        qs = qs.filter(action=f["action"])
    start, end = _parse_day(f["date_from"]), _parse_day(f["date_to"], end=True)
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lt=end)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    _related(list(page.object_list))
    admins = list(base.order_by().values_list("actor_email", flat=True).distinct().order_by("actor_email"))
    actions = [(a, action_label(a)) for a in base.order_by().values_list("action", flat=True).distinct().order_by("action")]
    return render(request, "dashboard/activity.html", {
        "page": page, "f": f, "admins": admins, "actions": actions, "can_clear": activity_service.can_clear(user), "show_cleared": show_cleared,
        "filtered": any(f.values()), "total": page.paginator.count, "today": timezone.localdate().isoformat(),
    })


def _clear_params(data):
    mode = data.get("mode", "")
    ids = []
    for chunk in data.getlist("ids"):
        ids += [int(x) for x in str(chunk).split(",") if x.strip().isdigit() and len(x.strip()) <= 12]
    return mode, ids, data.get("before", "")


@perm("library.clear_activity")
def activity_clear_count(request):
    """How many records an option would clear - shown in the confirmation dialog. Same checks as the real operation."""
    mode, ids, before = _clear_params(request.GET)
    try:
        p = activity_service.plan(request.user, mode, ids, before)
    except activity_service.ClearError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"count": p["count"], "protected_kept": p["protected_kept"]})


@perm("library.clear_activity")
@require_POST
def activity_clear(request):
    mode, ids, before = _clear_params(request.POST)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Clearing was not confirmed. Nothing was changed.")
        return redirect("activity")
    try:
        cleared, kept = activity_service.clear_activity(request.user, mode, ids, before, request.POST.get("expected_count"))
    except activity_service.ClearError as exc:
        messages.error(request, str(exc))
    else:
        note = f" {kept} protected security record{'s were' if kept != 1 else ' was'} kept." if kept else ""
        messages.success(request, f"Cleared {cleared} activity record{'s' if cleared != 1 else ''}.{note}")
    return redirect("activity")


@perm("library.clear_activity")
def activity_audit(request):
    page = Paginator(SecurityAuditLog.objects.select_related("actor"), 50).get_page(request.GET.get("page"))
    return render(request, "dashboard/activity_audit.html", {"page": page})


@perm("accounts.manage_settings")
def settings_view(request):
    obj = SiteSetting.load()
    form = SettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.save()
        log_activity(request.user, "settings_update", "settings", 1, "Updated site settings")
        messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(request, "dashboard/settings.html", {"form": form})


@perm("accounts.export_library")
def backup(request):
    return render(request, "dashboard/backup.html", {
        "skills": Skill.objects.count(), "attachments": SkillAttachment.objects.count(), "categories": Category.objects.count(),
    })


@perm("accounts.export_library")
def export_library(request):
    fmt = request.GET.get("format", "zip")
    log_activity(request.user, "export", "library", None, f"Exported the full library ({fmt})")
    return exports.backup_json_response() if fmt == "json" else exports.backup_zip_response()
