from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView, PasswordResetView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from library.activity import log_activity

from . import ratelimit
from .forms import ActivationForm, EmailLoginForm
from .models import User

GENERIC_ERROR = "Incorrect e-mail address or password."


@never_cache
def login_view(request):
    if request.user.is_authenticated and request.user.has_admin_access:
        return redirect("dashboard")
    form = EmailLoginForm(request.POST or None)
    status = 200
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        if ratelimit.is_locked(request, email):
            form.add_error(None, "Too many failed attempts. Please wait 15 minutes and try again.")
            status = 429
        else:
            user = authenticate(request, username=email, password=form.cleaned_data["password"])
            if user is not None and user.has_admin_access:
                ratelimit.reset(request, email)
                login(request, user)
                log_activity(user, "login", "user", user.pk, "Signed in")
                if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                    next_url = ""
                if user.must_change_password:
                    return redirect("password_change")  # temporary password: choose a new one first
                return redirect(next_url or "dashboard")
            ratelimit.record_failure(request, email)
            form.add_error(None, GENERIC_ERROR)
    return render(request, "accounts/login.html", {"form": form, "next": next_url}, status=status)


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, "logout", "user", request.user.pk, "Signed out")
    logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("home")


@never_cache
def activate_view(request, uidb64, token):
    """One-time account activation / password reset link issued by a Super Administrator."""
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is None or not user.is_active or not default_token_generator.check_token(user, token):
        return render(request, "accounts/activate_invalid.html", status=400)
    form = ActivationForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        log_activity(user, "password_set", "user", user.pk, "Password set through activation link")
        messages.success(request, "Your password is set. You can sign in now.")
        return redirect("login")
    return render(request, "accounts/activate.html", {"form": form, "account": user})


# ---- Forgot password: Django's built-in reset flow (signed, expiring, single-use links) -------------
class ManagementPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        # Django already limits this to active accounts with a usable password; keep management accounts only.
        return (u for u in super().get_users(email) if u.has_admin_access)


class ForgotPasswordView(PasswordResetView):
    form_class = ManagementPasswordResetForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        if not ratelimit.reset_request_allowed(self.request, form.cleaned_data["email"]):
            return redirect(self.success_url)  # same page, nothing sent
        return super().form_valid(form)  # the response never reveals whether the address has an account


class ResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        User.objects.filter(pk=form.user.pk).update(must_change_password=False)
        log_activity(form.user, "password_reset", "user", form.user.pk, "Password changed through a reset link")
        return response


def reset_done(request):
    return render(request, "accounts/password_reset_done.html", {"email_configured": settings.EMAIL_CONFIGURED})


def reset_complete(request):
    return render(request, "accounts/password_reset_complete.html")


# ---- Change password (mandatory while a temporary password is in use; optional afterwards) ----------
class ManagementPasswordChangeForm(PasswordChangeForm):
    def clean_new_password1(self):
        new = self.cleaned_data.get("new_password1")
        if new and self.user.check_password(new):
            raise forms.ValidationError("Choose a password that is different from your current one.")
        return new


@login_required(login_url="login")
@never_cache
def password_change_view(request):
    user = request.user
    if not user.has_admin_access:
        return redirect("login")
    forced = user.must_change_password
    form = ManagementPasswordChangeForm(user, request.POST or None)
    if request.method == "POST":
        if ratelimit.is_locked(request, user.email):
            form.add_error(None, "Too many failed attempts. Please wait 15 minutes and try again.")
        elif form.is_valid():
            form.save()
            User.objects.filter(pk=user.pk).update(must_change_password=False)
            update_session_auth_hash(request, form.user)  # this session stays signed in; any other session is signed out
            log_activity(user, "password_change", "user", user.pk, "Changed own password")
            messages.success(request, "Your password was changed.")
            return redirect("dashboard")
        elif "old_password" in form.errors:
            ratelimit.record_failure(request, user.email)
    return render(request, "accounts/password_change.html", {"form": form, "forced": forced})
