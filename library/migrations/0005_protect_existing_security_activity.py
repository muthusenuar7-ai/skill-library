from django.db import migrations
from django.db.models import Q

# Same rule as library.activity_rules.PROTECTED_PREFIXES, frozen here so the migration never changes meaning later.
PREFIXES = ("user_", "password_", "dev_password", "role_", "activity_clear", "settings_update", "export")


def protect_existing(apps, schema_editor):
    ActivityLog = apps.get_model("library", "ActivityLog")
    q = Q()
    for prefix in PREFIXES:
        q |= Q(action__startswith=prefix)
    ActivityLog.objects.filter(q).update(is_protected=True)  # nothing is deleted or archived


class Migration(migrations.Migration):
    dependencies = [("library", "0004_activity_archiving_and_security_audit")]
    operations = [migrations.RunPython(protect_existing, migrations.RunPython.noop)]
