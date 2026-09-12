__all__ = ()

from apps.documents.models import DocumentType, Template
from django import forms


class Step1Form(forms.Form):
    source_text = forms.CharField(
        label="Черновик документа",
        widget=forms.Textarea(
            attrs={
                "rows": 14,
                "placeholder": "Вставьте черновик как есть...",
                "id": "id_source_text",
            }
        ),
        min_length=5,
        error_messages={
            "required": "Введите черновик документа.",
            "min_length": "Слишком короткий текст — нужно минимум 5 символов.",
        },
    )


class Step2Form(forms.Form):
    document_type = forms.ModelChoiceField(
        label="Тип документа",
        queryset=DocumentType.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
    )
    template = forms.ModelChoiceField(
        label="Шаблон оформления",
        queryset=Template.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document_type"].queryset = DocumentType.objects.filter(
            is_active=True
        )
        self.fields["template"].queryset = Template.objects.filter(is_active=True)
