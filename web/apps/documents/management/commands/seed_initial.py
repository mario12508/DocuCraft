__all__ = ()

from django.core.management.base import BaseCommand

from apps.documents.models import DocumentType, RequiredField, Template


DOCUMENT_TYPES = [
    {
        "code": "sluzhebnaya_zapiska",
        "name": "Служебная записка",
        "structure_hint": "кому → от кого → суть → подпись",
        "order": 1,
        "fields": [
            ("addressee", "Адресат", ["addressee_position", "addressee_name"]),
            ("sender", "От кого", ["author_position", "author_name"]),
            ("subject", "Тема", ["topic"]),
            ("date", "Дата", ["date"]),
        ],
    },
    {
        "code": "dokladnaya_zapiska",
        "name": "Докладная записка",
        "structure_hint": "кому → от кого → суть → подпись",
        "order": 2,
        "fields": [
            ("addressee", "Адресат", ["addressee_position", "addressee_name"]),
            ("sender", "От кого", ["author_position", "author_name"]),
            ("subject", "Тема", ["topic"]),
            ("date", "Дата", ["date"]),
        ],
    },
    {
        "code": "informacionnaya_spravka",
        "name": "Информационная справка",
        "structure_hint": "заголовок → основной текст → подпись",
        "order": 3,
        "fields": [
            ("addressee", "Адресат", ["addressee_position", "addressee_name"]),
            ("sender", "От кого", ["author_position", "author_name"]),
            ("subject", "Тема", ["topic"]),
            ("date", "Дата", ["date"]),
        ],
    },
    {
        "code": "pismo",
        "name": "Письмо",
        "structure_hint": "адресат → обращение → суть → подпись",
        "order": 4,
        "fields": [
            ("addressee", "Адресат", ["addressee_position", "addressee_name"]),
            ("sender", "От кого", ["author_position", "author_name"]),
            ("subject", "Тема", ["topic"]),
            ("date", "Дата", ["date"]),
        ],
    },
]

TEMPLATES = [
    {
        "code": "classic",
        "name": "Классический",
        "description": "Times New Roman 14, поля 3/1.5/2/2, интервал 1.5",
        "rules": {
            "page": {
                "size": "A4",
                "margins": {"top": 2, "bottom": 2, "left": 3, "right": 1.5},
            },
            "font": {"name": "Times New Roman", "size": 14},
            "line_spacing": 1.5,
            "paragraph": {
                "first_line_indent": 1.25,
                "alignment": "justify",
            },
            "header": "",
            "footer": "",
        },
    },
    {
        "code": "modern",
        "name": "Современный",
        "description": "Calibri 12, поля 2/2/2/2, интервал 1.15",
        "rules": {
            "page": {
                "size": "A4",
                "margins": {"top": 2, "bottom": 2, "left": 2, "right": 2},
            },
            "font": {"name": "Calibri", "size": 12},
            "line_spacing": 1.15,
            "paragraph": {
                "first_line_indent": 0.0,
                "alignment": "left",
            },
            "header": "",
            "footer": "",
        },
    },
]


class Command(BaseCommand):
    help = "Заводит стартовые типы документов, шаблоны и реквизиты"

    def handle(self, *args, **options):
        self.stdout.write("Типы документов:")
        for dt in DOCUMENT_TYPES:
            fields = dt.pop("fields")
            doc_type, created = DocumentType.objects.update_or_create(
                code=dt["code"], defaults=dt,
            )
            for order_num, (code, label, ai_source) in enumerate(fields,
                                                                 start=1):
                RequiredField.objects.update_or_create(
                    document_type=doc_type, code=code,
                    defaults={"label": label, "order": order_num,
                              "ai_source": ai_source},
                )
            mark = "+" if created else "~"
            self.stdout.write(f"  [{mark}] {doc_type.name}")

        self.stdout.write("\nШаблоны:")
        for tpl in TEMPLATES:
            _, created = Template.objects.update_or_create(
                code=tpl["code"], defaults=tpl,
            )
            mark = "+" if created else "~"
            self.stdout.write(f"  [{mark}] {tpl['name']}")

        self.stdout.write(self.style.SUCCESS("\nГотово."))