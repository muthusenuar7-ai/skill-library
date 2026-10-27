"""Role definitions: two Django Groups carrying explicit permissions.

Super Admin -> group with every management permission (NOT Django's is_superuser).
Admin       -> group with skill/import/publish permissions only.
"""
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.management import create_contenttypes

from .models import LEGACY_GROUP_NAMES, ROLE_ADMIN, ROLE_LABELS, ROLE_SUPER

ADMIN_PERMISSIONS = [
    ("library", "view_skill"),
    ("library", "add_skill"),
    ("library", "change_skill"),
    ("library", "publish_skill"),
    ("library", "import_skills"),
    ("library", "view_category"),
    ("library", "add_category"),  # needed so imports can map new folders to categories
    ("library", "view_activitylog"),  # Admins see skill/import/category activity only (filtered in the view)
]
SUPER_EXTRA_PERMISSIONS = [
    ("library", "change_category"),
    ("library", "clear_activity"),  # Super Admin only: archive activity history (never protected security records)
    ("accounts", "manage_team"),
    ("accounts", "manage_settings"),
    ("accounts", "export_library"),
    ("accounts", "view_user"),
    ("accounts", "add_user"),
    ("accounts", "change_user"),
]


def _perms(pairs):
    found = []
    for app_label, codename in pairs:
        try:
            found.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        except Permission.DoesNotExist:
            pass
    return found


def ensure_roles():
    """Create/refresh the role groups. Safe to call repeatedly."""
    for old, new in LEGACY_GROUP_NAMES.items():  # rename groups from the first release instead of duplicating them
        if Group.objects.filter(name=old).exists() and not Group.objects.filter(name=new).exists():
            Group.objects.filter(name=old).update(name=new)
    admin_group, _ = Group.objects.get_or_create(name=ROLE_LABELS[ROLE_ADMIN])
    super_group, _ = Group.objects.get_or_create(name=ROLE_LABELS[ROLE_SUPER])
    admin_perms = _perms(ADMIN_PERMISSIONS)
    admin_group.permissions.set(admin_perms)
    super_group.permissions.set(admin_perms + _perms(SUPER_EXTRA_PERMISSIONS))
    return admin_group, super_group


def sync_roles_after_migrate(sender, using="default", **kwargs):
    # Permissions of other apps may not exist yet when this app's post_migrate fires.
    for config in global_apps.get_app_configs():
        if config.models_module is not None:
            create_contenttypes(config, verbosity=0, using=using)
            create_permissions(config, verbosity=0, using=using)
    ensure_roles()


def set_role(user, role):
    """Assign exactly one management role through groups. Never grants Django's is_superuser/is_staff."""
    admin_group, super_group = ensure_roles()
    if role == ROLE_SUPER:
        groups = [super_group]
    elif role == ROLE_ADMIN:
        groups = [admin_group]
    else:
        raise ValueError(f"Unknown role: {role!r}")
    user.groups.set(groups)
    user.clear_role_cache()
    return user
