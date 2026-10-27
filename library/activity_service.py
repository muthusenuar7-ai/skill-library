"""Clearing (archiving) activity history. The ONLY code path that clears activity.

Rules enforced here, not in the views or templates:
  * only a Super Admin holding library.clear_activity may clear anything;
  * clearing ARCHIVES records (soft delete) - nothing is physically removed;
  * protected security records (role/account/password/settings/export events, earlier clearing events) are never touched;
  * the number of records the person confirmed must equal the number that would be cleared, checked inside the transaction;
  * every operation is written to the protected SecurityAuditLog and to the activity feed.
"""
import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.models import ROLE_SUPER

from .activity import log_activity
from .models import ActivityLog, SecurityAuditLog

MAX_SELECTED = 1000
MODES = {"selected": SecurityAuditLog.Action.SELECTED, "older": SecurityAuditLog.Action.OLDER, "all": SecurityAuditLog.Action.ALL}


class ClearError(Exception):
    """A problem the person can fix (shown as a message)."""


def can_clear(user):
    return bool(user and user.is_authenticated and user.is_active and user.role == ROLE_SUPER and user.has_perm("library.clear_activity"))


def parse_cutoff(value):
    try:
        day = datetime.date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise ClearError("Choose a valid date.")
    if day > timezone.localdate():
        raise ClearError("Choose today or an earlier date - records from the future cannot be older than a date.")
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min)), day


def _scope(mode, ids, cutoff):
    """(everything the operation targets that is not yet cleared, the subset that may actually be cleared)."""
    live = ActivityLog.objects.filter(archived_at__isnull=True)
    if mode == "selected":
        live = live.filter(pk__in=ids)
    elif mode == "older":
        live = live.filter(created_at__lt=cutoff)
    return live, live.filter(is_protected=False)


def plan(user, mode, ids=(), before=None):
    """Counts for the confirmation dialog. Same checks and the same query as the real operation."""
    if not can_clear(user):
        raise PermissionDenied
    if mode not in MODES:
        raise ClearError("Unknown clearing option.")
    cutoff = parse_cutoff(before)[0] if mode == "older" else None
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_SELECTED] if mode == "selected" else []
    if mode == "selected" and not ids:
        raise ClearError("Select at least one activity record first.")
    targeted, eligible = _scope(mode, ids, cutoff)
    return {"count": eligible.count(), "protected_kept": targeted.filter(is_protected=True).count(), "cutoff": cutoff, "ids": ids}


def clear_activity(user, mode, ids=(), before=None, expected_count=None):
    """Archive the eligible records and return (cleared, protected_kept)."""
    if not can_clear(user):
        raise PermissionDenied
    if mode not in MODES:
        raise ClearError("Unknown clearing option.")
    try:
        expected = int(expected_count)
    except (TypeError, ValueError):
        raise ClearError("The confirmation is missing its record count. Please start again.")
    with transaction.atomic():
        p = plan(user, mode, ids, before)
        if p["count"] != expected:
            raise ClearError(f"The number of records changed while you were confirming (you confirmed {expected}, now {p['count']}). Nothing was cleared - please review and try again.")
        if p["count"] == 0:
            raise ClearError("There are no clearable activity records for that choice.")
        _, eligible = _scope(mode, p["ids"], p["cutoff"])
        cleared = eligible.update(archived_at=timezone.now(), archived_by=user)  # soft delete; protected rows are excluded by the query
        detail = {"selected": f"{len(p['ids'])} record(s) selected", "older": f"older than {before}", "all": "all eligible records"}[mode]
        SecurityAuditLog.objects.create(actor=user, actor_email=user.email, action=MODES[mode], records_affected=cleared, protected_kept=p["protected_kept"], detail=detail)
        log_activity(user, "activity_cleared", "activity", None, f"{MODES[mode].label}: {cleared} record(s) cleared ({detail}); {p['protected_kept']} protected record(s) kept")
    return cleared, p["protected_kept"]
