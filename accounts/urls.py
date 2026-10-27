from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("change-password/", views.password_change_view, name="password_change"),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="password_reset"),
    path("forgot-password/sent/", views.reset_done, name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", views.reset_complete, name="password_reset_complete"),
    path("activate/<uidb64>/<token>/", views.activate_view, name="activate"),
]
