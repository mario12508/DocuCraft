"""Сквозные сценарии через вьюхи. AI замокан, реальные вызовы не идут."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.documents.models import Document, DocumentType, Template
from apps.documents.services.ai_processor import AIError, AIResult

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="tester", password="tester")


@pytest.fixture
def base_data(db):
    dt = DocumentType.objects.create(
        code="test_type", name="Тестовый тип", order=1,
    )
    tpl = Template.objects.create(
        code="test_tpl", kind="system", name="Тестовый шаблон",
        rules={"organization": "ООО «Тест»",
               "font": {"name": "Arial", "size": 12}},
        placeholders=[
            {"placeholder": "[Кому]", "code": "addressee_position",
             "label": "Адресат"},
            {"placeholder": "[Дата]", "code": "date", "label": "Дата"},
            {"placeholder": "[Текст документа]", "code": "body",
             "label": "Текст"},
        ],
    )
    return dt, tpl


@pytest.mark.django_db
class TestFullPath:
    """Сценарий 1: полный путь до DOCX."""

    def test_step1_to_step3(self, client, user, base_data):
        client.force_login(user)
        dt, tpl = base_data

        fake = AIResult(
            processed_text="Обработанный служебный текст.",
            extracted_fields={
                "addressee_position": "Директору",
                "date": "01.01.2025",
                "topic": "О тесте",
            },
            provider="mock",
            model="mock-1",
        )

        with patch("apps.documents.views.process_draft", return_value=fake):
            r = client.post(
                reverse("documents:step1"),
                {"source_text": "тестовый черновик"},
            )
            assert r.status_code == 302

            r = client.post(
                reverse("documents:step2"),
                {"document_type": dt.pk, "template": tpl.pk},
            )
            assert r.status_code == 302

        doc = Document.objects.latest("pk")
        assert doc.status == "ready"
        assert doc.processed_text == "Обработанный служебный текст."
        assert doc.ai_provider == "mock"
        assert doc.extracted_fields["addressee_position"] == "Директору"

    def test_docx_download(self, client, user, base_data):
        client.force_login(user)
        dt, tpl = base_data

        fake = AIResult(
            processed_text="Текст.",
            extracted_fields={"addressee_position": "Директору"},
            provider="mock", model="mock-1",
        )

        with patch("apps.documents.views.process_draft", return_value=fake):
            client.post(reverse("documents:step1"),
                        {"source_text": "черновик"})
            client.post(reverse("documents:step2"),
                        {"document_type": dt.pk, "template": tpl.pk})

        doc = Document.objects.latest("pk")
        r = client.get(reverse("documents:download", args=[doc.pk]))

        assert r.status_code == 200
        assert r["Content-Type"].startswith(
            "application/vnd.openxmlformats"
        )

        content = b"".join(r.streaming_content)
        assert content[:2] == b"PK"
        assert len(content) > 1000


@pytest.mark.django_db
class TestScenario6Errors:
    """Сценарий 6: недоступность ИИ."""

    def test_ai_error_preserves_draft(self, client, user, base_data):
        client.force_login(user)
        dt, tpl = base_data

        with patch(
            "apps.documents.views.process_draft",
            side_effect=AIError("Все провайдеры недоступны"),
        ):
            client.post(reverse("documents:step1"),
                        {"source_text": "важный оригинал"})
            client.post(reverse("documents:step2"),
                        {"document_type": dt.pk, "template": tpl.pk})

        doc = Document.objects.latest("pk")
        assert doc.status == "error"
        assert "провайдеры" in doc.error_message
        assert doc.source_text == "важный оригинал"
        assert doc.processed_text == "важный оригинал"


@pytest.mark.django_db
class TestScenario3MissingFields:
    """Сценарий 3: пустые обязательные реквизиты."""

    def test_missing_field_detected(self, client, user, base_data):
        client.force_login(user)
        dt, tpl = base_data

        fake = AIResult(
            processed_text="Текст без адресата.",
            extracted_fields={"date": "01.01.2025"},
            provider="mock", model="mock-1",
        )

        with patch("apps.documents.views.process_draft", return_value=fake):
            client.post(reverse("documents:step1"),
                        {"source_text": "черновик"})
            client.post(reverse("documents:step2"),
                        {"document_type": dt.pk, "template": tpl.pk})

        doc = Document.objects.latest("pk")
        assert "addressee_position" in doc.missing_fields
