from django.shortcuts import redirect
from django.urls import reverse

ALLOWED_WHILE_CHANGING = ("/admin/change-password/", "/admin/logout/")


class PasswordChangeRequiredMiddleware:
    """While a temporary password is in use, the only management pages reachable are Change Password and Sign out.

    Applies to every management URL (and Django admin), so typing a dashboard address cannot skip the change.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path
        if (
            user is not None and user.is_authenticated and getattr(user, "must_change_password", False)
            and path.startswith(("/admin/", "/django-admin/")) and path not in ALLOWED_WHILE_CHANGING
        ):
            return redirect(reverse("password_change"))
        return self.get_response(request)
