__all__ = ()

from apps.dashboard import views
from django.urls import path

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
]
