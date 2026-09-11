__all__ = ()

from django.db import models


class DocumentType(models.Model):
    """Тип документа: служебная записка, докладная, справка, письмо."""

    code = models.SlugField("Код", unique=True)
    name = models.CharField("Название", max_length=100)
    structure_hint = models.TextField(
        "Подсказка по структуре", blank=True,
        help_text="Например: «кому → от кого → суть → подпись»",
    )
    is_active = models.BooleanField("Активен", default=True)
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Тип документа"
        verbose_name_plural = "Типы документов"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Template(models.Model):
    """Шаблон оформления организации."""

    code = models.SlugField("Код", unique=True)
    name = models.CharField("Название", max_length=100)
    description = models.TextField("Описание", blank=True)
    rules = models.JSONField("Правила оформления", default=dict, help_text="...")

    # НОВОЕ ПОЛЕ ↓
    docx_template = models.FileField(
        "Файл-шаблон DOCX",
        upload_to="templates/",
        blank=True,
        help_text=(
            "Файл .docx с плейсхолдерами [Кому], [Должность], [ФИО], "
            "[Дата], [Номер], [Заголовок], [Текст документа]. "
            "Если не задан — используется программная сборка."
        ),
    )

    is_active = models.BooleanField("Активен", default=True)


class RequiredField(models.Model):
    """Обязательный реквизит для типа документа."""

    document_type = models.ForeignKey(
        DocumentType, on_delete=models.CASCADE,
        related_name="required_fields", verbose_name="Тип документа",
    )
    code = models.SlugField("Код реквизита")
    label = models.CharField("Название", max_length=100)
    placeholder = models.CharField(
        "Плейсхолдер", max_length=100, default="[Заполнить]",
    )
    order = models.PositiveIntegerField("Порядок", default=0)
    ai_source = models.JSONField(
        "Ключи из ответа ИИ", default=list, blank=True,
        help_text=(
            "Список ключей из JSON-ответа ИИ, которые нужно склеить "
            "в этот реквизит. Например: [\"addressee_position\", "
            "\"addressee_name\"]. Если пусто — используется code."
        ),
    )

    class Meta:
        verbose_name = "Обязательный реквизит"
        verbose_name_plural = "Обязательные реквизиты"
        ordering = ["order"]
        unique_together = ("document_type", "code")

    def __str__(self):
        return f"{self.document_type.name} — {self.label}"


class Document(models.Model):
    """Сформированный документ (история)."""

    STATUS_CHOICES = [
        ("draft", "Черновик"),
        ("processing", "Обработка"),
        ("ready", "Готов"),
        ("error", "Ошибка"),
    ]

    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, verbose_name="Тип документа",
    )
    template = models.ForeignKey(
        Template, on_delete=models.PROTECT, verbose_name="Шаблон",
    )
    source_text = models.TextField("Исходный черновик")
    processed_text = models.TextField("Обработанный текст", blank=True)
    extracted_fields = models.JSONField(
        "Извлечённые реквизиты", default=dict,
    )
    missing_fields = models.JSONField(
        "Недостающие реквизиты", default=list,
    )
    docx_file = models.FileField(
        "DOCX-файл", upload_to="documents/%Y/%m/%d/", blank=True,
    )
    status = models.CharField(
        "Статус", max_length=20, choices=STATUS_CHOICES, default="draft",
    )
    error_message = models.TextField("Сообщение об ошибке", blank=True)

    # НОВОЕ: информация о том, какой провайдер сработал
    ai_provider = models.CharField(
        "ИИ-провайдер", max_length=50, blank=True,
    )
    ai_model = models.CharField(
        "ИИ-модель", max_length=100, blank=True,
    )

    # НОВОЕ: дата формирования документа (автозаполнение)
    document_date = models.DateField(
        "Дата документа", null=True, blank=True,
    )

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    def recalc_status(self):
        if self.status == "error":
            return "error"
        if self.missing_fields:
            return "draft"
        return "ready"

    def get_labelled_fields(self):
        """Возвращает [(label, value, is_missing), ...] для шаблона."""
        extracted = self.extracted_fields or {}
        result = []
        for rf in self.document_type.required_fields.all():
            value = extracted.get(rf.code, "")
            result.append({
                "label": rf.label,
                "value": value,
                "is_missing": not value,
            })
        return result

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.document_type.name} от {self.created_at:%d.%m.%Y %H:%M}"
