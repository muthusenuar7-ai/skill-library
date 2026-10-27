from django.conf import settings

from .models import SiteSetting


def site(request):
    ctx = {"SITE_NAME": "Datacense AI Skill Library", "COMPANY": "Datacense", "COLLECTION_MAX": settings.COLLECTION_MAX_SKILLS}
    if not request.path.startswith(("/admin/", "/django-admin/")):
        ctx["site_settings"] = SiteSetting.load()
    return ctx
