__all__ = ()

from apps.documents.models import Document, DocumentType, Template
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import DeleteView, DetailView, ListView, TemplateView


class Home(LoginRequiredMixin, TemplateView):
    """Главная страница рабочей панели."""

    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Главная"
        context["page_subtitle"] = "Подготовка служебных документов за 3 шага"
        context["document_types"] = DocumentType.objects.filter(is_active=True)
        context["templates"] = Template.objects.filter(is_active=True)
        return context


class DocumentListView(LoginRequiredMixin, ListView):
    """Список созданных документов."""

    model = Document
    template_name = "dashboard/documents/list.html"
    context_object_name = "documents"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            Document.objects
            .select_related("document_type", "template")
            .order_by("-created_at")
        )
        status = self.request.GET.get("status")
        if status in dict(Document.STATUS_CHOICES):
            qs = qs.filter(status=status)
        dt = self.request.GET.get("type")
        if dt:
            qs = qs.filter(document_type__code=dt)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Документы"
        context["page_subtitle"] = "История сформированных документов"
        context["status_choices"] = Document.STATUS_CHOICES
        context["document_types"] = DocumentType.objects.filter(is_active=True)
        context["current_status"] = self.request.GET.get("status", "")
        context["current_type"] = self.request.GET.get("type", "")
        return context


class DocumentDetailView(LoginRequiredMixin, DetailView):
    """Карточка одного документа."""

    model = Document
    template_name = "dashboard/documents/detail.html"
    context_object_name = "document"

    def get_queryset(self):
        return Document.objects.select_related("document_type", "template")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = f"Документ #{self.object.pk}"
        context["page_subtitle"] = self.object.document_type.name
        return context


class DocumentDeleteView(LoginRequiredMixin, DeleteView):
    """Удаление документа."""

    model = Document
    success_url = reverse_lazy("dashboard:documents")

    def form_valid(self, form):
        messages.success(self.request, "Документ удалён.")
        return super().form_valid(form)
