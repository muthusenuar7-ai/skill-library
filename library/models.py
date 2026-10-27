import uuid

from django.conf import settings
from django.core.validators import validate_slug
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse

from .activity_rules import is_protected_action
from .storage import get_private_storage
from .utils import clean_tags, content_fingerprint, normalize_text, safe_filename, unique_slug


class Category(models.Model):
    """Two-level taxonomy: a Major Domain (parent is NULL) may have Subcategories (parent = a domain).

    Skills point at their domain through `Skill.category` (unchanged from the flat design) and optionally at one
    of that domain's subcategories through `Skill.subcategory`.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(Lower("name"), condition=Q(parent__isnull=True), name="unique_domain_name_ci"),
            models.UniqueConstraint(Lower("name"), "parent", condition=Q(parent__isnull=False), name="unique_subcategory_name_ci"),
        ]

    def __str__(self):
        return f"{self.parent.name} / {self.name}" if self.parent_id else self.name

    def clean(self):
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A category cannot be its own parent."})
            if self.parent.parent_id:
                raise ValidationError({"parent": "Only two levels are supported: choose a major domain as the parent."})
            if self.pk and (self.children.exists() or self.skills.exists()):
                raise ValidationError({"parent": "A domain that has subcategories or skills cannot become a subcategory."})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        if not self.slug:
            base = f"{self.parent.name} {self.name}" if self.parent_id else self.name
            self.slug = unique_slug(Category, base, exclude_pk=self.pk, max_length=80)
        super().save(*args, **kwargs)

    @property
    def is_subcategory(self):
        return self.parent_id is not None

    def get_absolute_url(self):
        return reverse("category", args=[self.slug])


class SkillQuerySet(models.QuerySet):
    def public(self):
        """The ONLY queryset any public route may use."""
        return self.filter(status=Skill.Status.PUBLISHED, category__is_active=True).select_related("category")


def skill_upload_to(instance, filename):
    return f"skills/{uuid.uuid4().hex}/{safe_filename(filename, 'upload')}"


def attachment_upload_to(instance, filename):
    return f"attachments/{uuid.uuid4().hex}/{safe_filename(filename, 'file')}"


class Skill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True, blank=True, validators=[validate_slug])
    description = models.CharField(max_length=500, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="skills", verbose_name="major domain")
    subcategory = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT, related_name="subcategory_skills")
    content = models.TextField(help_text="Markdown instructions, stored exactly as written or imported.")
    tags = models.CharField(max_length=300, blank=True, help_text="Comma separated.")
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_file = models.FileField(upload_to=skill_upload_to, storage=get_private_storage, blank=True, max_length=300)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_featured = models.BooleanField(default=False)
    source_ref = models.CharField(max_length=80, blank=True, db_index=True, help_text="Original catalogue ID, e.g. XL-01.")
    content_hash = models.CharField(max_length=64, blank=True, db_index=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="skills_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SkillQuerySet.as_manager()

    class Meta:
        ordering = ["title"]
        permissions = [
            ("publish_skill", "Can publish, unpublish and archive skills"),
            ("import_skills", "Can import skills"),
        ]
        indexes = [
            models.Index(fields=["status", "category"], name="skill_status_category_idx"),
            models.Index(fields=["status", "-updated_at"], name="skill_status_updated_idx"),
            models.Index(fields=["title"], name="skill_title_idx"),
            models.Index(fields=["status", "subcategory"], name="skill_status_sub_idx"),
            models.Index(fields=["status", "-created_at"], name="skill_status_created_idx"),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        if self.subcategory_id and self.subcategory.parent_id != self.category_id:
            raise ValidationError({"subcategory": "The subcategory must belong to the selected major domain."})

    def save(self, *args, **kwargs):
        if self.subcategory_id and self.subcategory.parent_id != self.category_id:
            self.subcategory = None  # never keep a subcategory from another domain
        self.content = normalize_text(self.content) if self.content else ""
        self.content_hash = content_fingerprint(self.content)
        self.tags = clean_tags(self.tags)
        if not self.slug:
            self.slug = unique_slug(Skill, self.title, exclude_pk=self.pk)
        for attempt in range(3):  # tolerate a concurrent insert grabbing the same slug
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if attempt == 2 or self.pk:
                    raise
                self.slug = unique_slug(Skill, self.slug)

    def get_absolute_url(self):
        return reverse("skill_detail", args=[self.slug])

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def is_public(self):
        return self.status == self.Status.PUBLISHED and self.category.is_active


class SkillAttachment(models.Model):
    """Supporting file of a packaged skill (e.g. templates beside SKILL.md)."""

    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="attachments")
    path = models.CharField(max_length=240, help_text="Relative path inside the skill package.")
    file = models.FileField(upload_to=attachment_upload_to, storage=get_private_storage, max_length=300)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["path"]
        constraints = [models.UniqueConstraint(fields=["skill", "path"], name="unique_attachment_path_per_skill")]

    def __str__(self):
        return f"{self.skill.slug}/{self.path}"


class ProtectedRecordError(Exception):
    """Raised when something tries to delete a record that must be kept."""


class ActivityQuerySet(models.QuerySet):
    def delete(self):
        if self.filter(is_protected=True).exists():
            raise ProtectedRecordError("Protected security activity cannot be deleted.")
        return super().delete()


class AppendOnlyQuerySet(models.QuerySet):
    def delete(self):
        raise ProtectedRecordError("Security audit records cannot be deleted.")

    def update(self, **kwargs):
        raise ProtectedRecordError("Security audit records cannot be changed.")


class ActivityLog(models.Model):
    """One thing an administrator did. Clearing archives (soft-deletes) a record; security-relevant ones are protected."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="activity")
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=50, db_index=True)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_protected = models.BooleanField(default=False, db_index=True, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = ActivityQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        permissions = [("clear_activity", "Can clear (archive) activity history")]
        indexes = [models.Index(fields=["archived_at", "-created_at"], name="activity_visible_idx")]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_email} {self.action}"

    def save(self, *args, **kwargs):
        self.is_protected = is_protected_action(self.action)  # derived from the action, never set by hand
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_protected:
            raise ProtectedRecordError("Protected security activity cannot be deleted.")
        return super().delete(*args, **kwargs)


class SecurityAuditLog(models.Model):
    """Separate, append-only record of every activity-clearing operation. No screen, button or service deletes these."""

    class Action(models.TextChoices):
        SELECTED = "clear_selected", "Clear Selected Activity"
        OLDER = "clear_older", "Clear Older Records"
        ALL = "clear_all", "Clear All Activity"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_email = models.CharField(max_length=254)
    action = models.CharField(max_length=20, choices=Action.choices)
    records_affected = models.PositiveIntegerField()
    protected_kept = models.PositiveIntegerField(default=0)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_email} {self.action} ({self.records_affected})"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ProtectedRecordError("Security audit records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedRecordError("Security audit records cannot be deleted.")


class SiteSetting(models.Model):
    """Single-row, Super-Admin-only application settings."""

    public_library_download_enabled = models.BooleanField("Allow visitors to download the whole published library", default=True)
    intro_text = models.CharField(
        max_length=400,
        default="Practical, ready-to-use instructions for everyday business work. Explore by category, tailor them to your needs, and download what you need.",
    )
    footer_note = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Site settings"
