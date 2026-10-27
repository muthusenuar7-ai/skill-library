from django.db import migrations

RENAMES = (("Super Administrator", "Super Admin"), ("Administrator", "Admin"))


def forward(apps, schema_editor):
    """First release made Super Admins is_superuser/is_staff and used longer group names.

    Roles now live in groups only: move members to the new-name groups and clear the framework flags.
    """
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("accounts", "User")
    for old, new in RENAMES:
        old_group = Group.objects.filter(name=old).first()
        if old_group is None:
            continue
        new_group, _ = Group.objects.get_or_create(name=new)
        for user in User.objects.filter(groups=old_group):
            user.groups.add(new_group)
        old_group.delete()
    super_group = Group.objects.filter(name="Super Admin").first()
    if super_group:
        User.objects.filter(groups=super_group).update(is_superuser=False, is_staff=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
