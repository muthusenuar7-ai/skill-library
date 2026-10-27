from django.conf import settings


class SecurityHeadersMiddleware:
    """Adds a strict Content-Security-Policy to every page except Django's own admin.

    Our templates use no inline scripts or styles, so 'self' is enough.
    """

    POLICY = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/django-admin/"):
            response.headers.setdefault("Content-Security-Policy", self.POLICY)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # Admin pages and anything user specific must not be cached by shared caches.
        if request.path.startswith(("/admin/", "/django-admin/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
