from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def admin_permission_required(*perms):
    """Server-side gate for every administrator view.

    Anonymous or inactive users go to the login page; authenticated users without every listed
    permission get a 403. Hiding buttons is never the only protection.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated or not user.is_active:
                return redirect_to_login(request.get_full_path())
            if not user.has_admin_access or not all(user.has_perm(p) for p in perms):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
