"""Small cache-based login throttle: per e-mail address and per client IP.

Uses Django's cache. The default local-memory cache is per process; with several Gunicorn
workers use a shared cache (database or Redis) for exact counting - see DEPLOYMENT.md.
"""
import hashlib

from django.conf import settings
from django.core.cache import cache


def _keys(request, email):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:32]
    return f"login-fail:email:{digest}", f"login-fail:ip:{ip}"


def is_locked(request, email):
    email_key, ip_key = _keys(request, email)
    return (
        cache.get(email_key, 0) >= settings.LOGIN_MAX_ATTEMPTS
        or cache.get(ip_key, 0) >= settings.LOGIN_MAX_ATTEMPTS_PER_IP
    )


def record_failure(request, email):
    for key in _keys(request, email):
        cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def reset(request, email):
    cache.delete(_keys(request, email)[0])


def reset_request_allowed(request, email, limit=5, ip_limit=20):
    """Counts every password-reset request; returns False once the hourly limit is passed (the caller still shows the same page)."""
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:32]
    ip = request.META.get("REMOTE_ADDR", "unknown")
    allowed = True
    for key, cap in ((f"reset-req:email:{digest}", limit), (f"reset-req:ip:{ip}", ip_limit)):
        cache.add(key, 0, 3600)
        try:
            n = cache.incr(key)
        except ValueError:
            cache.set(key, 1, 3600)
            n = 1
        allowed = allowed and n <= cap
    return allowed
