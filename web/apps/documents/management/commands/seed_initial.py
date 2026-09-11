__all__ = ()

from django.core.management.base import BaseCommand

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
        from pathlib import Path
        from django.conf import settings
        from django.core.files import File
        from django.db.models import ProtectedError

        # --- Чистим устаревшие шаблоны (те, что больше не в TEMPLATES) ---
        valid_codes = {tpl["code"] for tpl in TEMPLATES}
        for old in Template.objects.exclude(code__in=valid_codes):
            try:
                old.delete()
                self.stdout.write(self.style.WARNING(
                    f"Удалён устаревший шаблон: {old.code}"
                ))
            except ProtectedError:
                self.stdout.write(self.style.WARNING(
                    f"Шаблон {old.code} не удалён — на него ссылаются документы. "
                    f"Переназначьте их в админке и запустите команду снова."
                ))

        # --- Типы документов ---
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

        # --- Шаблоны ---
        self.stdout.write("\nШаблоны:")
        templates_dir = getattr(settings, "DOCX_TEMPLATES_DIR", None)

        for tpl in TEMPLATES:
            # ВАЖНО: get, а не pop — иначе мутируем исходный словарь
            file_name = tpl.get("docx_template_name")

            # Копируем всё, кроме служебного имени файла
            defaults = {k: v for k, v in tpl.items() if k != "docx_template_name"}

            obj, created = Template.objects.update_or_create(
                code=tpl["code"], defaults=defaults,
            )

            if file_name and templates_dir:
                src = Path(templates_dir) / file_name
                if src.exists():
                    # Всегда перезаписываем файл — гарантируем актуальность
                    with open(src, "rb") as f:
                        obj.docx_template.save(file_name, File(f), save=True)
                    self.stdout.write(f"    привязан файл: {file_name}")
                else:
                    self.stdout.write(self.style.WARNING(
                        f"    файл не найден: {src}"
                    ))

            mark = "+" if created else "~"
            self.stdout.write(f"  [{mark}] {tpl['name']}")

        self.stdout.write(self.style.SUCCESS("\nГотово."))