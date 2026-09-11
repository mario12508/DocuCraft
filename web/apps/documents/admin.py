__all__ = ()

from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateFilter,
)
from unfold.decorators import display

from apps.documents.models import Document, DocumentType, RequiredField, Template


class RequiredFieldInline(TabularInline):
    model = RequiredField
    extra = 1
    fields = ("order", "code", "label", "placeholder")
    ordering = ("order",)
    tab = True


@admin.register(DocumentType)
class DocumentTypeAdmin(ModelAdmin):
    list_display = ("name", "code", "is_active", "order")
    list_editable = ("is_active", "order")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("order", "name")
    inlines = [RequiredFieldInline]
    compressed_fields = True

    fieldsets = (
        (
            "Основное",
            {
                "fields": ("code", "name", "structure_hint"),
                "classes": ("tab",),
            },
        ),
        (
            "Служебное",
            {
                "fields": ("is_active", "order"),
                "classes": ("tab",),
            },
        ),
    )


@admin.register(Template)
class TemplateAdmin(ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    compressed_fields = True

    fieldsets = (
        (
            "Основное",
            {
                "fields": ("code", "name", "description", "is_active"),
                "classes": ("tab",),
            },
        ),
        (
            "Правила оформления",
            {
                "fields": ("rules",),
                "classes": ("tab",),
                "description": (
                    "JSON-объект с параметрами: page, font, line_spacing, "
                    "paragraph, header, footer."
                ),
            },
        ),
    )


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = (
        "id",
        "document_type",
        "template",
        "status_col",
        "has_file_col",
        "created_at",
    )
    list_filter = (
        "document_type",
        "template",
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateFilter),
    )
    search_fields = ("source_text", "processed_text")
    readonly_fields = (
        "created_at",
        "updated_at",
        "error_message",
        "docx_file",
    )
    date_hierarchy = "created_at"
    compressed_fields = True
    list_per_page = 25

    fieldsets = (
        (
            "Связи",
            {
                "fields": ("document_type", "template", "status"),
                "classes": ("tab",),
            },
        ),
        (
            "Тексты",
            {
                "fields": ("source_text", "processed_text"),
                "classes": ("tab",),
            },
        ),
        (
            "Реквизиты",
            {
                "fields": ("extracted_fields", "missing_fields"),
                "classes": ("tab",),
            },
        ),
        (
            "Файл и служебное",
            {
                "fields": (
                    "docx_file",
                    "error_message",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("tab",),
            },
        ),
    )

    @display(description="Статус", ordering="status", label=True)
    def status_col(self, obj):
        mapping = {
            "draft": ("Черновик", "warning"),
            "processing": ("Обработка", "info"),
            "ready": ("Готов", "success"),
            "error": ("Ошибка", "danger"),
        }
        return mapping.get(obj.status, (obj.status, "default"))

    @display(description="Файл")
    def has_file_col(self, obj):
        if not obj.docx_file:
            return format_html('<span style="color:#9ca3af;">—</span>')
        return format_html(
            '<a href="{}" target="_blank">Скачать</a>',
            obj.docx_file.url,
        )