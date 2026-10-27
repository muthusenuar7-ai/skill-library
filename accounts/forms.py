from django import forms
from django.contrib.auth import password_validation

from .models import ROLE_ADMIN, ROLE_LABELS, ROLE_SUPER, User


class EmailLoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class ActivationForm(forms.Form):
    new_password1 = forms.CharField(label="New password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    new_password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("new_password1"), data.get("new_password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("new_password2", "The two passwords do not match.")
            else:
                try:
                    password_validation.validate_password(p1, self.user)
                except forms.ValidationError as exc:
                    self.add_error("new_password1", exc)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.save(update_fields=["password"])
        return self.user


ROLE_CHOICES = [(ROLE_ADMIN, ROLE_LABELS[ROLE_ADMIN]), (ROLE_SUPER, ROLE_LABELS[ROLE_SUPER])]


class TeamCreateForm(forms.Form):
    email = forms.EmailField()
    full_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=ROLE_ADMIN)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this e-mail address already exists.")
        return email


class TeamEditForm(forms.Form):
    full_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=ROLE_CHOICES)

    def __init__(self, *args, member=None, **kwargs):
        self.member = member
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.member.pk).exists():
            raise forms.ValidationError("Another account already uses this e-mail address.")
        return email
