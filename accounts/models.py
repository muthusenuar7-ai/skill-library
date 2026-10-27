from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

ROLE_SUPER = "super_admin"
ROLE_ADMIN = "admin"
ROLE_LABELS = {ROLE_SUPER: "Super Admin", ROLE_ADMIN: "Admin"}
LEGACY_GROUP_NAMES = {"Super Administrator": "Super Admin", "Administrator": "Admin"}


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        # Case-insensitive login lookup.
        return self.get(email__iexact=(email or "").strip())

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("An e-mail address is required.")
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        # Framework "break-glass" account for /django-admin/ only. It has NO management role:
        # application Super Admins are created with seed_team / Team Management and are never is_superuser.
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("e-mail address", unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can open Django's built-in admin (framework break-glass accounts only; never set for management roles).")
    date_joined = models.DateTimeField(default=timezone.now)
    must_change_password = models.BooleanField(default=False, help_text="Set while a temporary password is in use; the user must choose a new one at first login.")

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["email"]
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_user_email_ci")]
        permissions = [
            ("manage_team", "Can create and manage administrator accounts"),
            ("manage_settings", "Can change critical application settings"),
            ("export_library", "Can export and back up the library"),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email

    @property
    def role(self):
        """Management role from group membership only; Django's is_superuser is NOT a management role."""
        if not self.pk:
            return None
        cached = getattr(self, "_role_cache", False)
        if cached is False:
            names = set(self.groups.values_list("name", flat=True))
            cached = ROLE_SUPER if ROLE_LABELS[ROLE_SUPER] in names else (ROLE_ADMIN if ROLE_LABELS[ROLE_ADMIN] in names else None)
            self._role_cache = cached
        return cached

    def refresh_from_db(self, *args, **kwargs):
        self.clear_role_cache()
        super().refresh_from_db(*args, **kwargs)

    def clear_role_cache(self):
        self.__dict__.pop("_role_cache", None)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, "No role")

    @property
    def display_name(self):
        return self.full_name or self.email

    @property
    def is_super_admin(self):
        return self.is_active and self.role == ROLE_SUPER

    @property
    def has_admin_access(self):
        return self.is_active and self.role is not None
