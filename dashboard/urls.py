from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("skills/", views.manage_skills, name="manage_skills"),
    path("skills/new/", views.skill_create, name="skill_create"),
    path("skills/upload/", views.skill_upload, name="skill_upload"),
    path("skills/markdown-preview/", views.markdown_preview, name="markdown_preview"),
    path("skills/bulk/", views.skill_bulk, name="skill_bulk"),
    path("skills/<int:pk>/edit/", views.skill_edit, name="skill_edit"),
    path("skills/<int:pk>/preview/", views.skill_preview, name="skill_preview"),
    path("skills/<int:pk>/status/", views.skill_status, name="skill_status"),
    path("skills/<int:pk>/download/", views.skill_download_admin, name="skill_download_admin"),
    path("skills/<int:pk>/original/", views.skill_original_file, name="skill_original_file"),
    path("skills/<int:pk>/attachments/<int:att_id>/", views.attachment_admin, name="attachment_admin"),
    path("import/", views.import_skills, name="import_skills"),
    path("import/review/<str:token>/", views.import_review, name="import_review"),
    path("categories/", views.categories, name="manage_categories"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("team/", views.team, name="team"),
    path("team/<int:pk>/edit/", views.team_edit, name="team_edit"),
    path("team/<int:pk>/link/", views.team_link, name="team_link"),
    path("team/<int:pk>/toggle/", views.team_toggle, name="team_toggle"),
    path("settings/", views.settings_view, name="settings"),
    path("backup/", views.backup, name="backup"),
    path("activity/", views.activity, name="activity"),
    path("activity/clear/", views.activity_clear, name="activity_clear"),
    path("activity/clear/count/", views.activity_clear_count, name="activity_clear_count"),
    path("activity/audit/", views.activity_audit, name="activity_audit"),
    path("export/", views.export_library, name="export_library"),
]
