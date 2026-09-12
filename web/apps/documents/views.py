__all__ = ()

from datetime import datetime

import logging

from apps.documents.docx_generator import generate_docx
from apps.documents.forms import Step1Form, Step2Form
from apps.documents.models import Document, DocumentType, Template
from apps.documents.pdf_generator import generate_pdf
from apps.documents.services.ai_processor import AIError, process_draft
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import FormView

logger = logging.getLogger(__name__)

SESSION_DRAFT = "draft_source_text"
SESSION_TYPE = "draft_document_type_id"
SESSION_TEMPLATE = "draft_template_id"


# Коды основного текста — не показываем в форме, они идут в textarea
BODY_CODES = {"body", "processed_text", "document_text", "text"}


# ---------------------------------------------------------------------------
# Применение результата ИИ
# ---------------------------------------------------------------------------

def _apply_ai_result(document, result, doc_type):
    """Применяет AIResult к документу. Маппинг — по template.placeholders."""
    document.processed_text = result.processed_text
    document.ai_provider = result.provider
    document.ai_model = result.model

    extracted = dict(result.extracted_fields or {})

    # Автозаполнение даты
    if "date" in extracted or any(
        p.get("code") == "date"
        for p in (document.template.placeholders or [])
    ):
        if not extracted.get("date"):
            extracted["date"] = timezone.now().strftime("%d.%m.%Y")
            document.document_date = timezone.now().date()
        else:
            try:
                document.document_date = datetime.strptime(
                    extracted["date"], "%d.%m.%Y"
                ).date()
            except (ValueError, TypeError):
                document.document_date = timezone.now().date()

    document.extracted_fields = extracted

    document.missing_fields = [
        p["code"] for p in (document.template.placeholders or [])
        if p.get("code") and not extracted.get(p["code"])
    ]

    document.status = document.recalc_status()
    document.error_message = ""
    document.save()


# ---------------------------------------------------------------------------
# Сохранение изменений из формы
# ---------------------------------------------------------------------------

def _apply_post_changes(request, document):
    """Сохраняет значения для всех плейсхолдеров шаблона."""
    extracted = dict(document.extracted_fields or {})

    for p in (document.template.placeholders or []):
        code = p.get("code")
        if not code:
            continue
        raw = request.POST.get(f"field_{code}")
        if raw is None:
            continue
        value = raw.strip()
        if value:
            extracted[code] = value
        else:
            extracted.pop(code, None)

    document.extracted_fields = extracted

    document.missing_fields = [
        p["code"] for p in (document.template.placeholders or [])
        if p.get("code") and not extracted.get(p["code"])
    ]

    processed = request.POST.get("processed_text")
    if processed is not None:
        document.processed_text = processed

    if document.status != "error":
        document.status = document.recalc_status()

    document.save()
    return document


# ---------------------------------------------------------------------------
# Шаги 1-2
# ---------------------------------------------------------------------------

class Step1View(LoginRequiredMixin, FormView):
    template_name = "documents/step1.html"
    form_class = Step1Form

    def get_initial(self):
        initial = super().get_initial()
        initial["source_text"] = self.request.session.get(SESSION_DRAFT, "")
        return initial

    def form_valid(self, form):
        self.request.session[SESSION_DRAFT] = form.cleaned_data["source_text"]
        return redirect("documents:step2")


class Step2View(LoginRequiredMixin, FormView):
    template_name = "documents/step2.html"
    form_class = Step2Form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

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
            document_type=dt, template=tpl,
            source_text=source_text, processed_text=source_text,
            extracted_fields={},
            missing_fields=[],
            status="processing",
        )

        for key in (SESSION_DRAFT, SESSION_TYPE, SESSION_TEMPLATE):
            self.request.session.pop(key, None)

        try:
            result = process_draft(source_text, dt.name)
            _apply_ai_result(document, result, dt)
        except AIError as exc:
            document.status = "error"
            document.error_message = str(exc)
            document.missing_fields = [
                p["code"] for p in (tpl.placeholders or [])
                if p.get("code")
            ]
            document.save()

        return redirect("documents:preview", pk=document.pk)


# ---------------------------------------------------------------------------
# Шаг 3 — preview
# ---------------------------------------------------------------------------

class PreviewView(LoginRequiredMixin, View):
    template_name = "documents/preview.html"

    def get_document(self, pk):
        return get_object_or_404(
            Document.objects.select_related("document_type", "template"),
            pk=pk,
        )

    def build_required_fields(self, document):
        """
        Динамически: все плейсхолдеры шаблона, кроме body-кодов.
        is_required — если код есть среди RequiredField типа.
        """
        extracted = document.extracted_fields or {}
        tmpl = document.template

        required_codes = set(
            document.document_type.required_fields.values_list("code", flat=True)
        )

        fields = []
        seen = set()
        for p in (tmpl.placeholders or []):
            code = p.get("code")
            if not code or code in seen:
                continue
            if code in BODY_CODES:
                continue
            seen.add(code)
            fields.append({
                "code": code,
                "label": p.get("label") or code,
                "placeholder": p.get("placeholder") or "",
                "value": extracted.get(code, ""),
                "is_missing": not extracted.get(code),
                "is_required": code in required_codes,
            })
        return fields

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

        _apply_post_changes(request, document)

        if action == "download":
            return redirect("documents:download", pk=document.pk)

        if request.POST.get("format") == "pdf":
            url = reverse("documents:download", args=[document.pk])
            return redirect(f"{url}?format=pdf")

        if action == "retry":
            try:
                result = process_draft(
                    document.source_text, document.document_type.name,
                )
                _apply_ai_result(document, result, document.document_type)
                messages.success(request, "Документ успешно обработан.")
            except AIError as exc:
                document.status = "error"
                document.error_message = str(exc)
                document.save()
                messages.error(request, f"ИИ недоступен: {exc}")
            return redirect("documents:preview", pk=document.pk)

        messages.success(request, "Изменения сохранены.")
        return redirect("documents:preview", pk=document.pk)


# ---------------------------------------------------------------------------
# Скачивание
# ---------------------------------------------------------------------------

class DownloadView(LoginRequiredMixin, View):
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
            try:
                buffer = generate_pdf(document)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("PDF generation failed")
                return HttpResponse(
                    f"Не удалось сформировать PDF: {exc}",
                    status=500,
                    content_type="text/plain; charset=utf-8",
                )
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
            buffer, as_attachment=True,
            filename=filename, content_type=content_type,
        )


# ---------------------------------------------------------------------------
# Предпросмотр DOCX — отдельная вьюха, рендерится в iframe
# ---------------------------------------------------------------------------

class DocxPreviewView(LoginRequiredMixin, View):
    """Рендерит DOCX в HTML для предпросмотра в браузере."""

    def get(self, request, pk):
        try:
            import mammoth
        except ImportError:
            return HttpResponse(
                "Для предпросмотра установите mammoth: pip install mammoth",
                status=500,
                content_type="text/plain; charset=utf-8",
            )

        document = get_object_or_404(
            Document.objects.select_related("document_type", "template"),
            pk=pk,
        )
        docx_buffer = generate_docx(document)
        result = mammoth.convert_to_html(docx_buffer)

        return render(
            request,
            "documents/partials/_docx_preview.html",
            {
                "document": document,
                "html": result.value,
                "warnings": result.messages,
            },
        )
