from django.contrib import admin
from django.urls import include, path

# Django's built-in admin is restricted to Super Administrators (see accounts/admin.py).
admin.site.site_header = "Datacense - Django administration"
# Reachable only by framework superusers (created with `createsuperuser`); management roles never have these flags.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_staff and request.user.is_superuser

handler403 = "library.views.error_403"
handler404 = "library.views.error_404"
handler500 = "library.views.error_500"

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("admin/", include("accounts.urls")),
    path("admin/", include("dashboard.urls")),
    path("", include("library.urls")),
]
