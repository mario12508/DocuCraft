__all__ = ()

import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DeleteView, DetailView, ListView, TemplateView

from apps.documents.models import Document, DocumentType, Template
from apps.documents.services.ai_processor import pick_provider
from apps.documents.services.template_parser import parse_template


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

class TemplateListView(LoginRequiredMixin, ListView):
    model = Template
    template_name = "dashboard/templates/list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return (
            Template.objects
            .filter(is_active=True)
            .filter(models.Q(kind="system") | models.Q(owner=self.request.user))
            .order_by("kind", "name")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page_title"] = "Шаблоны"
        ctx["page_subtitle"] = "Системные и ваши загруженные шаблоны"
        ctx["system_templates"] = [t for t in ctx["templates"] if t.kind == "system"]
        ctx["user_templates"] = [t for t in ctx["templates"] if t.kind == "user"]
        return ctx

class TemplateUploadView(LoginRequiredMixin, View):
    template_name = "dashboard/templates/upload.html"

    def get(self, request):
        return render(request, self.template_name, {
            "page_title": "Загрузить шаблон",
            "page_subtitle": "Word-файл с плейсхолдерами [Кому], [Дата] и т.п.",
        })

    def post(self, request):
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        file = request.FILES.get("file")

        if not name:
            messages.error(request, "Укажите название шаблона.")
            return redirect("dashboard:template_upload")
        if not file:
            messages.error(request, "Загрузите .docx-файл.")
            return redirect("dashboard:template_upload")
        if not file.name.lower().endswith(".docx"):
            messages.error(request, "Поддерживаются только файлы .docx.")
            return redirect("dashboard:template_upload")

        # Уникальный code на основе имени
        base = re.sub(r"[^a-z0-9]+", "_", name.lower(), flags=re.IGNORECASE).strip("_")
        code = base or f"tpl_{request.user.pk}"
        suffix = 1
        while Template.objects.filter(owner=request.user, code=code).exists():
            suffix += 1
            code = f"{base}_{suffix}"

        # Создаём запись, чтобы получить pk для upload_to
        template = Template.objects.create(
            owner=request.user,
            kind="user",
            code=code,
            name=name,
            description=description,
            parse_status="processing",
        )
        template.docx_template.save(file.name, file, save=True)

        # Парсим плейсхолдеры
        provider = _pick_provider()
        placeholders, error = parse_template(template.docx_template, provider)
        template.placeholders = placeholders
        template.parse_status = "error" if error else "ready"
        template.parse_error = error or ""
        template.save()

        return redirect("dashboard:template_detail", pk=template.pk)

class TemplateDetailView(LoginRequiredMixin, View):
    template_name = "dashboard/templates/detail.html"

    def _get(self, request, pk):
        return get_object_or_404(
            Template, pk=pk,
        )

    def _can_edit(self, request, template):
        return template.kind == "user" and template.owner_id == request.user.pk

    def get(self, request, pk):
        template = self._get(request, pk)
        if not (template.kind == "system" or template.owner_id == request.user.pk):
            raise Http404
        return render(request, self.template_name, {
            "template": template,
            "can_edit": self._can_edit(request, template),
            "page_title": template.name,
        })

    def post(self, request, pk):
        template = self._get(request, pk)
        if not self._can_edit(request, template):
            raise Http404

        action = request.POST.get("action")

        if action == "update_placeholders":
            new = []
            for i, item in enumerate(template.placeholders):
                code = request.POST.get(f"code_{i}", "").strip()
                label = request.POST.get(f"label_{i}", "").strip()
                if code:
                    new.append({
                        "placeholder": item["placeholder"],
                        "code": code,
                        "label": label or code,
                    })
            template.placeholders = new
            template.save()
            messages.success(request, "Плейсхолдеры обновлены.")

        elif action == "reparse":
            provider = _pick_provider()
            placeholders, error = parse_template(template.docx_template, provider)
            template.placeholders = placeholders
            template.parse_status = "error" if error else "ready"
            template.parse_error = error or ""
            template.save()
            messages.success(request, "Шаблон переразобран.")

        elif action == "delete":
            template.delete()
            messages.success(request, "Шаблон удалён.")
            return redirect("dashboard:templates")

        return redirect("dashboard:template_detail", pk=template.pk)

def _pick_provider():
    """Возвращает первый доступный AI-провайдер или None."""
    providers = getattr(settings, "AI_PROVIDERS", [])
    for p in providers:
        if p.get("api_key"):
            return p
    return None