__all__ = ()

from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db.models import ProtectedError

from apps.documents.models import DocumentType, RequiredField, Template


DOCUMENT_TYPES = [
    {
        "code": "sluzhebnaya_zapiska",
        "name": "Служебная записка",
        "structure_hint": (
            "Адресат → Автор → Дата и номер → Заголовок → Текст → Подпись"
        ),
        "order": 1,
        "fields": [
            ("addressee", "Адресат",
             ["addressee_position", "addressee_name"]),
            ("sender", "От кого",
             ["author_position", "author_name"]),
            ("date", "Дата", ["date"]),
            ("number", "Номер", ["number"]),
            ("subject", "Заголовок", ["topic"]),
            ("signature", "Подпись",
             ["author_position", "author_name"]),
        ],
    },
    {
        "code": "dokladnaya_zapiska",
        "name": "Докладная записка",
        "structure_hint": (
            "Адресат → Автор → Дата и номер → Заголовок → Текст → Подпись"
        ),
        "order": 2,
        "fields": [
            ("addressee", "Адресат",
             ["addressee_position", "addressee_name"]),
            ("sender", "От кого",
             ["author_position", "author_name"]),
            ("date", "Дата", ["date"]),
            ("number", "Номер", ["number"]),
            ("subject", "Заголовок", ["topic"]),
            ("signature", "Подпись",
             ["author_position", "author_name"]),
        ],
    },
    {
        "code": "informacionnaya_spravka",
        "name": "Информационная справка",
        "structure_hint": (
            "Заголовок → Текст → Дата → Составитель → Подпись"
        ),
        "order": 3,
        "fields": [
            ("subject", "Заголовок", ["topic"]),
            ("date", "Дата", ["date"]),
            ("sender", "Составитель",
             ["author_position", "author_name"]),
            ("signature", "Подпись",
             ["author_position", "author_name"]),
        ],
    },
    {
        "code": "pismo",
        "name": "Письмо",
        "structure_hint": (
            "Адресат → Дата и номер → Тема → Обращение → Текст → Подпись"
        ),
        "order": 4,
        "fields": [
            ("addressee", "Адресат",
             ["addressee_position", "addressee_name"]),
            ("date", "Дата", ["date"]),
            ("number", "Номер", ["number"]),
            ("subject", "Тема", ["topic"]),
            ("sender", "Отправитель",
             ["author_position", "author_name"]),
            ("signature", "Подпись",
             ["author_position", "author_name"]),
        ],
    },
]


TEMPLATES = [
    {
        "code": "classic_corporate",
        "kind": "system",
        "name": "Классический корпоративный",
        "description": (
            "Times New Roman 14 pt, интервал 1.5, поля 3/1.5/2/2 см. "
            "Шапка справа, заголовок по центру, подпись слева, "
            "верхний колонтитул с названием организации."
        ),
        "docx_template_name": "clasic.docx",
        "rules": {
            "page": {
                "size": "A4",
                "margins": {"top": 2, "bottom": 2, "left": 3, "right": 1.5},
            },
            "font": {"name": "Times New Roman", "size": 14},
            "line_spacing": 1.5,
            "paragraph": {"first_line_indent": 1.25, "alignment": "justify"},
            "header": "ООО «Ромашка»",
            "header_font_size": 11,
            "footer": "",
            "layout": "classic",
            "signature_align": "left",
            "organization": "ООО «Ромашка»",
        },
    },
    {
        "code": "modern_regulatory",
        "kind": "system",
        "name": "Современный регламентный",
        "description": (
            "Arial 12 pt, интервал 1.15, поля 2.5/2/1.5/1.5 см. "
            "Табличная шапка «Кому / От кого», подпись по центру, "
            "нижний колонтитул с типом и датой документа."
        ),
        "docx_template_name": "modern.docx",
        "rules": {
            "page": {
                "size": "A4",
                "margins": {"top": 1.5, "bottom": 1.5,
                            "left": 2.5, "right": 2},
            },
            "font": {"name": "Arial", "size": 12},
            "line_spacing": 1.15,
            "paragraph": {"first_line_indent": 0, "alignment": "left"},
            "header": "",
            "footer": "{document_type} от {date}",
            "footer_font_size": 10,
            "layout": "modern",
            "signature_align": "center",
            "organization": "ООО «Ромашка»",
        },
    },
]


class Command(BaseCommand):
    help = "Заводит стартовые типы документов, шаблоны и реквизиты"

    def handle(self, *args, **options):
        self._seed_document_types()
        self._seed_system_templates()

    # ------------------------------------------------------------------
    # Типы документов
    # ------------------------------------------------------------------
    def _seed_document_types(self):
        self.stdout.write("Типы документов:")
        for dt in DOCUMENT_TYPES:
            fields = dt["fields"]
            payload = {k: v for k, v in dt.items() if k != "fields"}
            doc_type, created = DocumentType.objects.update_or_create(
                code=payload["code"], defaults=payload,
            )
            keep_codes = {code for code, _, _ in fields}
            doc_type.required_fields.exclude(code__in=keep_codes).delete()

            for order_num, (code, label, ai_source) in enumerate(fields, start=1):
                RequiredField.objects.update_or_create(
                    document_type=doc_type, code=code,
                    defaults={
                        "label": label,
                        "order": order_num,
                        "ai_source": ai_source,
                    },
                )
            mark = "+" if created else "~"
            self.stdout.write(f"  [{mark}] {doc_type.name}")

    # ------------------------------------------------------------------
    # Системные шаблоны
    # ------------------------------------------------------------------
    def _seed_system_templates(self):
        # Импорты внутри метода — чтобы seed не падал при отсутствии
        # ai_processor.py или template_parser.py в первые минуты разработки.
        try:
            from apps.documents.services.template_parser import parse_template
        except ImportError:
            parse_template = None

        try:
            from apps.documents.services.ai_processor import (
                pick_gigachat_provider,
                pick_openai_provider,
            )
        except ImportError:
            pick_gigachat_provider = None
            pick_openai_provider = None

        self.stdout.write("\nШаблоны (системные):")
        templates_dir = getattr(settings, "DOCX_TEMPLATES_DIR", None)

        # --- Чистим устаревшие системные шаблоны ---
        valid_codes = {tpl["code"] for tpl in TEMPLATES}
        for old in Template.objects.filter(kind="system").exclude(
            code__in=valid_codes
        ):
            try:
                old.delete()
                self.stdout.write(self.style.WARNING(
                    f"  Удалён устаревший шаблон: {old.code}"
                ))
            except ProtectedError:
                self.stdout.write(self.style.WARNING(
                    f"  Шаблон {old.code} не удалён — есть документы. "
                    f"Переназначьте их в админке и запустите команду снова."
                ))

        # --- Создаём/обновляем системные шаблоны ---
        for tpl in TEMPLATES:
            file_name = tpl.get("docx_template_name")
            defaults = {k: v for k, v in tpl.items()
                        if k != "docx_template_name"}

            obj, created = Template.objects.update_or_create(
                owner__isnull=True,
                code=tpl["code"],
                defaults=defaults,
            )

            if file_name and templates_dir:
                src = Path(templates_dir) / file_name
                if src.exists():
                    with open(src, "rb") as f:
                        obj.docx_template.save(file_name, File(f), save=True)
                    self.stdout.write(f"    привязан файл: {file_name}")

                    # --- Парсинг плейсхолдеров ---
                    if parse_template is not None:
                        try:
                            placeholders, error = parse_template(obj.docx_template, provider=None)
                            obj.placeholders = placeholders
                            obj.parse_status = "error" if error else "ready"
                            obj.parse_error = error or ""
                            obj.save(update_fields=[
                                "placeholders",
                                "parse_status",
                                "parse_error",
                            ])
                            self.stdout.write(
                                f"    найдено реквизитов: {len(placeholders)}"
                            )
                            if error:
                                self.stdout.write(self.style.WARNING(
                                    f"    парсинг: {error}"
                                ))
                        except Exception as exc:
                            obj.parse_status = "error"
                            obj.parse_error = f"Ошибка парсинга: {exc}"
                            obj.save(update_fields=[
                                "parse_status",
                                "parse_error",
                            ])
                            self.stdout.write(self.style.ERROR(
                                f"    ошибка парсинга: {exc}"
                            ))
                    else:
                        self.stdout.write(self.style.WARNING(
                            "    template_parser не найден — "
                            "placeholders не заполнены"
                        ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f"    файл не найден: {src}"
                    ))

            mark = "+" if created else "~"
            self.stdout.write(f"  [{mark}] {tpl['name']}")