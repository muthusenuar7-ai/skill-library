from .models import ActivityLog


def log_activity(actor, action, target_type="", target_id=None, description=""):
    return ActivityLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_email=getattr(actor, "email", "") or "system",
        action=action[:50],
        target_type=target_type[:50],
        target_id=target_id,
        description=description[:500],
    )
