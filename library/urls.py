from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("skills/", views.skill_list, name="skill_list"),
    path("skills/<slug:slug>/", views.skill_detail, name="skill_detail"),
    path("skills/<slug:slug>/download/", views.skill_download, name="skill_download"),
    path("skills/<slug:slug>/package/", views.skill_package_download, name="skill_package_download"),
    path("skills/<slug:slug>/files/<int:attachment_id>/", views.skill_attachment, name="skill_attachment"),
    path("categories/", views.categories_overview, name="categories"),
    path("categories/<slug:slug>/", views.category_detail, name="category"),
    path("favorites/", views.favorites, name="favorites"),
    path("favorites/cards/", views.favorites_cards, name="favorites_cards"),
    path("favorites/download/", views.favorites_download, name="favorites_download"),
    path("collection/", views.collection_page, name="collection"),
    path("collection/items/", views.collection_items, name="collection_items"),
    path("collection/check/", views.collection_check, name="collection_check"),
    path("collection/match/", views.collection_match, name="collection_match"),
    path("collection/download/", views.collection_download, name="collection_download"),
    path("help/", views.help_page, name="help"),
    path("about/", views.about_page, name="about"),
    path("downloads/category/<slug:slug>/", views.download_category, name="download_category"),
    path("downloads/all/", views.download_all, name="download_all"),
    path("downloads/selection/", views.download_selection, name="download_selection"),
]
