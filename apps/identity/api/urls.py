from django.urls import path

from . import views

auth_urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="auth-verify-email"),
    path("verify-email/resend/", views.ResendVerificationView.as_view(), name="auth-verify-resend"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("mfa/verify/", views.MFAVerifyView.as_view(), name="auth-mfa-verify"),
    path("token/refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("logout-all/", views.LogoutAllView.as_view(), name="auth-logout-all"),
    path("password/forgot/", views.PasswordForgotView.as_view(), name="auth-password-forgot"),
    path("password/reset/", views.PasswordResetView.as_view(), name="auth-password-reset"),
    path("password/change/", views.PasswordChangeView.as_view(), name="auth-password-change"),
    path("email/change/", views.EmailChangeView.as_view(), name="auth-email-change"),
    path("email/confirm/", views.EmailChangeConfirmView.as_view(), name="auth-email-confirm"),
    path("mfa/totp/setup/", views.TOTPSetupView.as_view(), name="auth-totp-setup"),
    path("mfa/totp/confirm/", views.TOTPConfirmView.as_view(), name="auth-totp-confirm"),
    path("mfa/totp/disable/", views.TOTPDisableView.as_view(), name="auth-totp-disable"),
    path("mfa/recovery-codes/", views.RecoveryCodesView.as_view(), name="auth-recovery-codes"),
]

me_urlpatterns = [
    path("", views.MeView.as_view(), name="me"),
    path("sessions/", views.SessionListView.as_view(), name="me-sessions"),
    path("sessions/<uuid:session_id>/", views.SessionDetailView.as_view(), name="me-session"),
]
