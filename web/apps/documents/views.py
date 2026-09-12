__all__ = ()

import logging
import re
from datetime import datetime

from apps.documents.docx_generator import generate_docx
from apps.documents.drafts_loader import load_draft_categories
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


BODY_CODES = {"body", "processed_text", "document_text", "text"}

# Служебные коды - заполняются не из AI
NON_AI_CODES = BODY_CODES | {"organization"}


# Маппинг: код (для шаблона и/или для UI) → список AI-ключей,
AI_FIELD_MAP = {
    # Для формы предпросмотра (склейки)
    "addressee": ["addressee_position", "addressee_name"],
    "sender": ["author_position", "author_name"],
    "signature": ["author_position", "author_name"],
    "subject": ["topic"],
    # Для шаблонов — раздельные коды
    "addressee_position": ["addressee_position"],
    "addressee_org": ["addressee_org"],
    "addressee_name": ["addressee_name"],
    "sender_position": ["author_position"],
    "sender_org": ["author_org"],
    "sender_name": ["author_name"],
    # Общие
    "date": ["date"],
    "number": ["number"],
}


# Утилиты
ORG_RE = re.compile(
    r"(ООО|АО|ЗАО|ОАО|ПАО|ИП)\s+"
    r'(?:[«"\']([^»"\']{2,60})[»"\']|([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,3}))'
)

NAME_RE = re.compile(r"([А-ЯЁ][а-яё]+)\s+([А-ЯЁ]\.\s*[А-ЯЁ]\.?)")


def _extract_name_from_source(source_text, keyword=None):
    """
    Ищет ФИО в строке адресата.
    Если keyword не передан — ищет по всем ключам ADDRESSEE_KEYS.
    """
    if not source_text:
        return None

    keys = (keyword,) if keyword else ADDRESSEE_KEYS

    for line in source_text.splitlines():
        low = line.lower()
        if not any(k in low for k in keys):
            continue
        if ":" not in line:
            continue

        body = line.split(":", 1)[1]
        m = NAME_RE.search(body)
        if m:
            return m.group(0).strip()

    return None


ADDRESSEE_KEYS = ("кому", "адресат", "получател")


def _extract_org_from_source(source_text):
    """
    Достаёт название организации ТОЛЬКО из строки адресата.
    Строка адресата — та, что начинается с «Кому:», «Адресат:»
    или «Получатель:». Если такой строки нет — возвращает None.
    """
    if not source_text:
        return None

    for line in source_text.splitlines():
        low = line.lower()
        if not any(k in low for k in ADDRESSEE_KEYS):
            continue
        if ":" not in line:
            continue

        # правая часть строки после «Кому:»
        body = line.split(":", 1)[1]
        m = ORG_RE.search(body)
        if not m:
            continue

        prefix = m.group(1)
        name = (m.group(2) or m.group(3) or "").strip()
        if name:
            return f"{prefix} «{name}»"

    return None


def _split_addressee(text):
    if not text:
        return None, None, None

    remaining = text.strip()
    org = None
    name = None

    m = ORG_RE.search(remaining)
    if m:
        prefix = m.group(1)
        body = (m.group(2) or m.group(3) or "").strip()
        if body:
            org = f"{prefix} «{body}»"
            remaining = remaining.replace(m.group(0), " ").strip()

    m = NAME_RE.search(remaining)
    if m:
        name = m.group(0).strip()
        remaining = remaining.replace(name, " ").strip()

    position = re.sub(r"\s+", " ", remaining).strip(" ,.")
    return position or None, org, name


def _clean_value(v):
    """Нормализует значение. Возвращает строку или None."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.lower() in ("null", "none", "нет данных", "нет", "-", "—"):
            return None
        return s
    if isinstance(v, (int, float)):
        return str(v)
    return None


def _parse_date(s):
    """Парсит строку ДД.ММ.ГГГГ → date. Иначе None."""
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


# Применение результата ИИ


def _apply_ai_result(document, result, doc_type):
    """
    Раскладывает AI-ответ в extracted_fields.
    Fallback'и (разбор склеенного адресата, орг из источника)
    выполняются ОДИН раз после основного маппинга, а не внутри цикла.
    """
    document.processed_text = result.processed_text
    document.ai_provider = result.provider
    document.ai_model = result.model

    ai = result.extracted_fields or {}
    extracted = {}

    # 1. Основной маппинг AI → extracted
    for code, ai_keys in AI_FIELD_MAP.items():
        parts = []
        for key in ai_keys:
            val = _clean_value(ai.get(key))
            if val:
                parts.append(val)
        if parts:
            extracted[code] = "\n".join(parts)

    # 2. Fallback: если адресат склеен в одну строку
    if not extracted.get("addressee_org") or not extracted.get("addressee_name"):
        raw = extracted.get("addressee_position") or extracted.get("addressee") or ""
        pos, org, name = _split_addressee(raw)
        if pos and not extracted.get("addressee_position"):
            extracted["addressee_position"] = pos
        if org and not extracted.get("addressee_org"):
            extracted["addressee_org"] = org
        if name and not extracted.get("addressee_name"):
            extracted["addressee_name"] = name

        # 3. Fallback: организация адресата из строки «Кому:»
        if not extracted.get("addressee_org"):
            org = _extract_org_from_source(document.source_text)
            if org:
                extracted["addressee_org"] = org

        # 4. Fallback: ФИО адресата из строки «Кому:»
        if not extracted.get("addressee_name"):
            name = _extract_name_from_source(document.source_text)
            if name:
                extracted["addressee_name"] = name

    # 5. Fallback для автора (должность и ФИО)
    if not extracted.get("sender_name"):
        raw = extracted.get("sender_position") or extracted.get("sender") or ""
        pos, _, name = _split_addressee(raw)
        if pos and not extracted.get("sender_position"):
            extracted["sender_position"] = pos
        if name:
            extracted["sender_name"] = name

    # 6. Организация-отправитель (шаблонный код organization)
    org = _clean_value((document.template.rules or {}).get("organization"))
    if org:
        extracted["organization"] = org

    # 7. Дата
    date_value = extracted.get("date")
    if not date_value:
        date_value = timezone.now().strftime("%d.%m.%Y")
        extracted["date"] = date_value
    document.document_date = _parse_date(date_value) or timezone.now().date()

    # 8. Номер - автогенерация, если AI не вернул
    if not extracted.get("number"):
        suffix = {
            "sluzhebnaya_zapiska": "СЗ",
            "dokladnaya_zapiska": "ДЗ",
            "pismo": "П",
        }.get(doc_type.code)
        if suffix:
            count = Document.objects.filter(document_type=doc_type).count()
            extracted["number"] = f"{count + 1:02d}-{suffix}"

    document.extracted_fields = extracted

    # 9. Недостающие реквизиты
    document.missing_fields = [
        p["code"]
        for p in (document.template.placeholders or [])
        if p.get("code") and p["code"] not in NON_AI_CODES and not extracted.get(p["code"])
    ]

    document.status = document.recalc_status()
    document.error_message = ""
    document.save()


# Сохранение изменений из формы


def _apply_post_changes(request, document):
    extracted = dict(document.extracted_fields or {})

    for p in document.template.placeholders or []:
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
        p["code"]
        for p in (document.template.placeholders or [])
        if p.get("code") and p["code"] not in NON_AI_CODES and not extracted.get(p["code"])
    ]

    processed = request.POST.get("processed_text")
    if processed is not None:
        document.processed_text = processed

    if document.status != "error":
        document.status = document.recalc_status()

    document.save()
    return document


# Шаги 1-2


class Step1View(LoginRequiredMixin, FormView):
    template_name = "documents/step1.html"
    form_class = Step1Form

    def get_initial(self):
        initial = super().get_initial()
        initial["source_text"] = self.request.session.get(SESSION_DRAFT, "")
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["draft_categories"] = load_draft_categories()
        return context

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
            document_type=dt,
            template=tpl,
            source_text=source_text,
            processed_text=source_text,
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
                p["code"]
                for p in (tpl.placeholders or [])
                if p.get("code") and p["code"] not in NON_AI_CODES
            ]
            document.save()

        return redirect("documents:preview", pk=document.pk)


# Шаг 3 — preview


class PreviewView(LoginRequiredMixin, View):
    template_name = "documents/preview.html"

    def get_document(self, pk):
        return get_object_or_404(
            Document.objects.select_related("document_type", "template"),
            pk=pk,
        )

    def build_required_fields(self, document):
        extracted = document.extracted_fields or {}
        tmpl = document.template

        required_codes = set(document.document_type.required_fields.values_list("code", flat=True))

        fields = []
        seen = set()
        for p in tmpl.placeholders or []:
            code = p.get("code")
            if not code or code in seen:
                continue
            if code in BODY_CODES:
                continue
            seen.add(code)
            fields.append(
                {
                    "code": code,
                    "label": p.get("label") or code,
                    "placeholder": p.get("placeholder") or "",
                    "value": extracted.get(code, ""),
                    "is_missing": not extracted.get(code),
                    "is_required": code in required_codes,
                }
            )
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
                    document.source_text,
                    document.document_type.name,
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


# Скачивание


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
                logger.exception("PDF generation failed")
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
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        return FileResponse(
            buffer,
            as_attachment=True,
            filename=filename,
            content_type=content_type,
        )


# Предпросмотр DOCX — отдельная вьюха, рендерится в iframe


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


class DocumentPreviewPDFView(LoginRequiredMixin, View):
    """
    PDF-предпросмотр готового документа.
    Генерирует тот же DOCX, что и при скачивании, и конвертирует в PDF.
    """

    def get(self, request, pk):
        from apps.documents.pdf_generator import PDFError, generate_pdf

        document = get_object_or_404(
            Document.objects.select_related("document_type", "template"),
            pk=pk,
        )

        try:
            pdf_buffer = generate_pdf(document)
        except PDFError as exc:
            return HttpResponse(
                f"Предпросмотр недоступен: {exc}",
                status=503,
                content_type="text/plain; charset=utf-8",
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("Document PDF preview failed")
            return HttpResponse(
                f"Ошибка предпросмотра: {exc}",
                status=500,
                content_type="text/plain; charset=utf-8",
            )

        response = HttpResponse(pdf_buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="document_{document.pk}_preview.pdf"'
        return response
