__all__ = ()

from apps.documents import views
from django.urls import path

app_name = "documents"

urlpatterns = [
    path("step1/", views.Step1View.as_view(), name="step1"),
    path("step2/", views.Step2View.as_view(), name="step2"),
    path("<int:pk>/preview/", views.PreviewView.as_view(), name="preview"),
    path("<int:pk>/preview-docx/", views.DocxPreviewView.as_view(), name="preview_docx"),
    path("<int:pk>/download/", views.DownloadView.as_view(), name="download"),
    path(
        "<int:pk>/preview-pdf/",
        views.DocumentPreviewPDFView.as_view(),
        name="preview_pdf",
    ),
]
