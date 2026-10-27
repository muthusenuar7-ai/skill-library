import os

from django import forms
from django.conf import settings
from django.core.validators import validate_slug

from library.models import Category, SiteSetting, Skill
from library.utils import clean_tags, make_slug, unique_slug

UPLOAD_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".json", ".zip"}


class ParentAwareSelect(forms.Select):
    """Subcategory <select> whose options carry data-parent so the browser can show only the chosen domain's options."""

    parent_map = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opt = super().create_option(name, value, label, selected, index, subindex, attrs)
        key = getattr(value, "value", value)
        if key in self.parent_map:
            opt["attrs"]["data-parent"] = str(self.parent_map[key])
        return opt


class SkillForm(forms.ModelForm):
    slug = forms.CharField(max_length=80, required=False, help_text="Leave empty to generate it from the title.")
    new_category = forms.CharField(max_length=120, required=False, label="...or a new major domain", help_text="Only if none of the existing domains fits.")
    new_subcategory = forms.CharField(max_length=120, required=False, label="...or a new subcategory", help_text="Created under the selected (or new) domain.")
    source_filename = forms.CharField(max_length=255, required=False, widget=forms.HiddenInput)

    class Meta:
        model = Skill
        fields = ["title", "slug", "description", "category", "new_category", "subcategory", "new_subcategory", "tags", "content", "uploaded_file", "status", "is_featured", "source_filename"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "content": forms.Textarea(attrs={"rows": 22, "spellcheck": "true", "class": "mono"}),
            "tags": forms.TextInput(attrs={"placeholder": "excel, formulas, beginner"}),
        }
        help_texts = {"tags": "Comma separated, up to 10.", "content": "Markdown. Raw HTML and scripts are removed when displayed."}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        domains = Category.objects.filter(parent__isnull=True).order_by("display_order", "name")
        subs = Category.objects.filter(parent__isnull=False).select_related("parent").order_by("parent__name", "name")
        self.fields["category"].queryset = domains
        self.fields["category"].required = False
        self.fields["category"].empty_label = "Choose a major domain..."
        self.fields["category"].label = "Major domain"
        self.fields["subcategory"].queryset = subs
        self.fields["subcategory"].required = False
        self.fields["subcategory"].empty_label = "No subcategory"
        widget = ParentAwareSelect(attrs=self.fields["subcategory"].widget.attrs)
        widget.parent_map = {s.pk: s.parent_id for s in subs}
        self.fields["subcategory"].widget = widget
        widget.choices = self.fields["subcategory"].choices  # a replaced widget must be handed the field's choices again
        self.fields["content"].required = False  # may come from an uploaded .md file
        self.fields["uploaded_file"].label = "Original file (optional)"

    def clean_title(self):
        title = " ".join(self.cleaned_data["title"].split())
        if len(title) < 3:
            raise forms.ValidationError("Title must be at least 3 characters.")
        return title

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip().lower()
        if not slug:
            return ""
        validate_slug(slug)
        if Skill.objects.filter(slug__iexact=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another skill already uses this slug.")
        return slug

    def clean_tags(self):
        raw = self.cleaned_data.get("tags", "")
        if len([t for t in raw.split(",") if t.strip()]) > 10:
            raise forms.ValidationError("Use at most 10 tags.")
        return clean_tags(raw)

    def clean_uploaded_file(self):
        f = self.cleaned_data.get("uploaded_file")
        if f and hasattr(f, "size"):
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in UPLOAD_EXTENSIONS:
                raise forms.ValidationError(f"File type {ext or '(none)'} is not allowed.")
            if f.size > settings.SKILL_UPLOAD_MAX_BYTES:
                raise forms.ValidationError("File is too large.")
        return f

    def clean(self):
        data = super().clean()
        content = (data.get("content") or "").replace("\x00", "")
        f = data.get("uploaded_file")
        if not content.strip() and f and hasattr(f, "read") and f.name.lower().endswith((".md", ".txt")):
            try:
                content = f.read().decode("utf-8-sig")
                f.seek(0)
            except UnicodeDecodeError:
                self.add_error("uploaded_file", "The file is not valid UTF-8 text.")
        if not content.strip():
            self.add_error("content", "Add Markdown content, or upload a .md file to read it from.")
        if len(content) > 500_000:
            self.add_error("content", "Content is too long (limit 500,000 characters).")
        data["content"] = content
        new_name = " ".join((data.get("new_category") or "").split())
        if not data.get("category"):
            if not new_name:
                self.add_error("category", "Choose a category or enter a new one.")
            elif self.user is not None and not self.user.has_perm("library.add_category"):
                self.add_error("new_category", "Your account cannot create categories. Pick an existing one.")
        data["new_category"] = new_name
        sub, new_sub = data.get("subcategory"), " ".join((data.get("new_subcategory") or "").split())
        data["new_subcategory"] = new_sub
        cat = data.get("category")
        if sub and cat and sub.parent_id != cat.pk:
            self.add_error("subcategory", "That subcategory belongs to a different major domain.")
        if sub and not cat and new_name:
            self.add_error("subcategory", "Pick an existing subcategory only together with its existing domain.")
        if new_sub and self.user is not None and not self.user.has_perm("library.add_category"):
            self.add_error("new_subcategory", "Your account cannot create categories. Pick an existing subcategory.")
        return data

    def save(self, commit=True):
        skill = super().save(commit=False)
        if not self.cleaned_data.get("category") and self.cleaned_data.get("new_category"):
            name = self.cleaned_data["new_category"]
            cat = Category.objects.filter(name__iexact=name).first() or Category.objects.filter(slug=make_slug(name, "category")).first()
            skill.category = cat or Category.objects.create(name=name, display_order=Category.objects.count() + 1)
        new_sub = self.cleaned_data.get("new_subcategory")
        if new_sub and not self.cleaned_data.get("subcategory"):
            existing = Category.objects.filter(parent=skill.category, name__iexact=new_sub).first()
            skill.subcategory = existing or Category.objects.create(name=new_sub, parent=skill.category, display_order=skill.category.children.count() + 1)
        src = os.path.basename(self.cleaned_data.get("source_filename") or "")
        if src and not skill.original_filename:
            skill.original_filename = src[:255]
        if not skill.slug:
            skill.slug = unique_slug(Skill, skill.title, exclude_pk=skill.pk)
        f = self.cleaned_data.get("uploaded_file")
        if f and hasattr(f, "name") and not getattr(f, "_committed", True):
            skill.original_filename = os.path.basename(f.name)[:255]
        if commit:
            skill.save()
        return skill


class CategoryForm(forms.ModelForm):
    slug = forms.CharField(max_length=80, required=False, help_text="Leave empty to generate it from the name.")

    class Meta:
        model = Category
        fields = ["name", "parent", "slug", "description", "display_order", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        labels = {"parent": "Parent major domain"}
        help_texts = {"parent": "Leave empty to create a major domain. Choose a domain to create a subcategory under it."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        domains = Category.objects.filter(parent__isnull=True).order_by("name")
        if self.instance.pk:
            domains = domains.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = domains
        self.fields["parent"].empty_label = "None - this is a major domain"
        self.fields["parent"].required = False

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip().lower()
        if not slug:
            return ""
        validate_slug(slug)
        if Category.objects.filter(slug__iexact=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another category already uses this slug.")
        return slug

    def clean(self):
        data = super().clean()
        name = " ".join((data.get("name") or "").split())
        parent = data.get("parent")
        if name and Category.objects.filter(name__iexact=name, parent=parent).exclude(pk=self.instance.pk).exists():
            self.add_error("name", "A category with this name already exists here.")
        data["name"] = name
        return data

    def _post_clean(self):
        super()._post_clean()  # runs Category.clean(): two levels only, no moving domains that hold skills

    def save(self, commit=True):
        cat = super().save(commit=False)
        if not cat.slug:
            base = f"{cat.parent.name} {cat.name}" if cat.parent_id else cat.name
            cat.slug = unique_slug(Category, make_slug(base, "category"), exclude_pk=cat.pk)
        if commit:
            cat.save()
        return cat


class SettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSetting
        fields = ["public_library_download_enabled", "intro_text", "footer_note"]
        labels = {"intro_text": "Introduction shown on the public About page", "footer_note": "Footer note (optional)"}
        widgets = {"intro_text": forms.Textarea(attrs={"rows": 3})}
