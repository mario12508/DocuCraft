__all__ = ()

from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.Home.as_view(), name="home"),

    path("documents/", views.DocumentListView.as_view(), name="documents"),
    path(
        "documents/<int:pk>/",
        views.DocumentDetailView.as_view(),
        name="document_detail",
    ),
    path(
        "documents/<int:pk>/delete/",
        views.DocumentDeleteView.as_view(),
        name="document_delete",
    ),

    path("templates/", views.TemplateListView.as_view(), name="templates"),
    path(
        "templates/upload/",
        views.TemplateUploadView.as_view(),
        name="template_upload",
    ),
    path(
        "templates/<int:pk>/",
        views.TemplateDetailView.as_view(),
        name="template_detail",
    ),
    path(
        "templates/<int:pk>/preview-pdf/",
        views.TemplatePreviewPDFView.as_view(),
        name="template_preview_pdf",
    ),
]