__all__ = ()

from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import FormView

from .docx_generator import generate_docx
from .forms import Step1Form, Step2Form
from .models import Document, DocumentType, Template
from .pdf_generator import generate_pdf


SESSION_DRAFT = "draft_source_text"
SESSION_TYPE = "draft_document_type_id"
SESSION_TEMPLATE = "draft_template_id"


def _apply_post_changes(request, document):
    extracted = dict(document.extracted_fields or {})

    for rf in document.document_type.required_fields.all():
        raw = request.POST.get(f"field_{rf.code}")
        if raw is None:
            continue
        value = raw.strip()
        if value:
            extracted[rf.code] = value
        else:
            extracted.pop(rf.code, None)

    document.extracted_fields = extracted
    document.missing_fields = [
        rf.code
        for rf in document.document_type.required_fields.all()
        if not extracted.get(rf.code)
    ]

    processed = request.POST.get("processed_text")
    if processed is not None:
        document.processed_text = processed

    # Автопересчёт только при обычном сохранении
    if document.status != "error":
        document.status = document.recalc_status()

    document.save()
    return document


class Step1View(FormView):
    """Шаг 1. Ввод черновика."""

    template_name = "documents/step1.html"
    form_class = Step1Form

    def get_initial(self):
        initial = super().get_initial()
        initial["source_text"] = self.request.session.get(SESSION_DRAFT, "")
        return initial

    def form_valid(self, form):
        self.request.session[SESSION_DRAFT] = form.cleaned_data["source_text"]
        return redirect("documents:step2")


class Step2View(FormView):
    """Шаг 2. Выбор типа документа и шаблона."""

    template_name = "documents/step2.html"
    form_class = Step2Form

    def dispatch(self, request, *args, **kwargs):
        if SESSION_DRAFT not in request.session:
            messages.info(request, "Сначала введите черновик.")
            return redirect("documents:step1")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        dt_id = self.request.session.get(SESSION_TYPE)
        tpl_id = self.request.session.get(SESSION_TEMPLATE)
        if dt_id:
            initial["document_type"] = dt_id
        if tpl_id:
            initial["template"] = tpl_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["source_text"] = self.request.session.get(SESSION_DRAFT, "")
        context["document_types"] = DocumentType.objects.filter(is_active=True)
        context["templates"] = Template.objects.filter(is_active=True)
        return context

    def form_valid(self, form):
        dt = form.cleaned_data["document_type"]
        tpl = form.cleaned_data["template"]
        source_text = self.request.session.get(SESSION_DRAFT, "")

        document = Document.objects.create(
            document_type=dt,
            template=tpl,
            source_text=source_text,
            processed_text=source_text,
            extracted_fields={},
            missing_fields=[f.code for f in dt.required_fields.all()],
            status="draft",
        )

        # Чистим сессию после создания документа
        for key in (SESSION_DRAFT, SESSION_TYPE, SESSION_TEMPLATE):
            self.request.session.pop(key, None)

        return redirect("documents:preview", pk=document.pk)

        return redirect("documents:preview", pk=document.pk)


class PreviewView(View):
    template_name = "documents/preview.html"

    def get_document(self, pk):
        return get_object_or_404(
            Document.objects.select_related("document_type", "template"),
            pk=pk,
        )

    def build_required_fields(self, document):
        extracted = document.extracted_fields or {}
        return [
            {
                "code": rf.code,
                "label": rf.label,
                "placeholder": rf.placeholder,
                "value": extracted.get(rf.code, ""),
                "is_missing": not extracted.get(rf.code),
            }
            for rf in document.document_type.required_fields.all()
        ]

    def get(self, request, pk):
        document = self.get_document(pk)
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "required_fields": self.build_required_fields(document),
            },
        )

    def post(self, request, pk):
        document = self.get_document(pk)
        action = (request.POST.get("action") or "save").strip()

        # 1) Явное переключение статуса — БЕЗ пересчёта
        if action == "mark_ready":
            document.status = "ready"
            document.save(update_fields=["status"])
            messages.success(request, "Документ отмечен как готовый.")
            return redirect("documents:preview", pk=document.pk)

        if action == "mark_draft":
            document.status = "draft"
            document.save(update_fields=["status"])
            messages.info(request, "Документ переведён в черновик.")
            return redirect("documents:preview", pk=document.pk)

        # 2) Обычное сохранение правок + автопересчёт статуса
        _apply_post_changes(request, document)

        if action == "download":
            return redirect("documents:download", pk=document.pk)

        if request.POST.get("format") == "pdf":
            url = reverse("documents:download", args=[document.pk])
            return redirect(f"{url}?format=pdf")

        messages.success(request, "Изменения сохранены.")
        return redirect("documents:preview", pk=document.pk)


class DownloadView(View):
    """Генерирует DOCX или PDF и отдаёт файл."""

    def get(self, request, pk):
        document = get_object_or_404(Document, pk=pk)
        return self._serve(document, request.GET.get("format", "docx"))

    def post(self, request, pk):
        document = get_object_or_404(Document, pk=pk)
        document = _apply_post_changes(request, document)
        return self._serve(document, request.POST.get("format", "docx"))

    def _serve(self, document, fmt):
        fmt = (fmt or "docx").lower()

        if fmt == "pdf":
            buffer = generate_pdf(document)
            filename = f"document_{document.pk}.pdf"
            content_type = "application/pdf"
        else:
            buffer = generate_docx(document)
            filename = f"document_{document.pk}.docx"
            content_type = (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            )

        return FileResponse(
            buffer,
            as_attachment=True,
            filename=filename,
            content_type=content_type,
        )