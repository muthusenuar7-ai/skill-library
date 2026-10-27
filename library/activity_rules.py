"""Pure rules about activity records (no model imports, so models.py can use them).

PROTECTED actions are security-relevant: the ordinary Clear tools never archive or delete them.
"""

# Anything that changes who can do what, how someone proves who they are, settings, exports, or activity clearing itself.
PROTECTED_PREFIXES = ("user_", "password_", "dev_password", "role_", "activity_clear", "settings_update", "export")


def is_protected_action(action):
    return str(action or "").startswith(PROTECTED_PREFIXES)


ACTION_LABELS = {
    "login": "Signed in", "logout": "Signed out",
    "skill_create": "Skill created", "skill_update": "Skill edited", "skill_publish": "Skill published", "skill_unpublish": "Skill unpublished",
    "skill_archive": "Skill archived", "skill_restore": "Skill restored",
    "skill_bulk_publish": "Bulk publish", "skill_bulk_unpublish": "Bulk unpublish", "skill_bulk_archive": "Bulk archive",
    "skill_bulk_restore": "Bulk restore", "skill_bulk_feature": "Bulk feature", "skill_bulk_unfeature": "Bulk un-feature",
    "import": "Skill imported", "import_replace": "Skill replaced by import", "classify": "Subcategories assigned",
    "category_create": "Category created", "category_update": "Category edited",
    "user_create": "Account created", "user_update": "Account edited", "user_role_change": "Role changed", "user_deactivate": "Account deactivated",
    "user_reactivate": "Account reactivated", "user_link": "Invitation / reset issued",
    "password_set": "Password set (activation)", "password_reset": "Password reset", "password_change": "Password changed", "dev_password_init": "Development passwords initialised",
    "settings_update": "Settings changed", "export": "Library exported", "activity_cleared": "Activity cleared",
}


def action_label(action):
    return ACTION_LABELS.get(action, str(action).replace("_", " ").capitalize())
