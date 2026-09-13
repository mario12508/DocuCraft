"""Тесты парсинга JSON и нормализации значений от ИИ."""

import pytest

from apps.documents.services.ai_processor import (
    _clean_json,
    _clean_value,
    _filter_fields,
)


class TestCleanJson:
    """_clean_json должен переваривать любой мусор вокруг JSON."""

    def test_plain_object(self):
        assert _clean_json('{"a": 1}') == {"a": 1}

    def test_markdown_block(self):
        raw = '```json\n{"addressee_name": "Иванов И.И."}\n```'
        assert _clean_json(raw)["addressee_name"] == "Иванов И.И."

    def test_markdown_without_lang(self):
        raw = '```\n{"a": 1}\n```'
        assert _clean_json(raw) == {"a": 1}

    def test_leading_and_trailing_text(self):
        raw = 'Вот результат:\n{"topic": "О тесте"}\nГотово.'
        assert _clean_json(raw)["topic"] == "О тесте"

    def test_control_chars_inside_string(self):
        raw = '{"body": "первая строка\nвторая строка"}'
        assert "первая строка" in _clean_json(raw)["body"]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _clean_json("")

    def test_garbage_raises(self):
        with pytest.raises(Exception):
            _clean_json("не json вообще без скобок")


class TestCleanValue:
    """_clean_value приводит «пустые» ответы модели к None."""

    def test_none(self):
        assert _clean_value(None) is None

    def test_empty_string(self):
        assert _clean_value("") is None
        assert _clean_value("   ") is None

    def test_null_like_strings(self):
        for s in ("null", "NULL", "None", "нет данных", "нет", "-", "—"):
            assert _clean_value(s) is None, f"должно быть None: {s!r}"

    def test_normal_string(self):
        assert _clean_value("  Иванов И.И.  ") == "Иванов И.И."

    def test_number(self):
        assert _clean_value(180000) == "180000"


class TestFilterFields:
    """_filter_fields отсекает body и пустые значения."""

    def test_drops_body(self):
        result = _filter_fields({"body": "текст", "topic": "О тесте"})
        assert "body" not in result
        assert result["topic"] == "О тесте"

    def test_drops_none_and_null_like(self):
        data = {
            "addressee_name": None,
            "addressee_org": "null",
            "addressee_position": "Директору",
        }
        result = _filter_fields(data)
        assert result == {"addressee_position": "Директору"}

    def test_keeps_all_valid(self):
        data = {
            "addressee_position": "Директору",
            "addressee_org": "ООО «Ромашка»",
            "date": "12.03.2025",
        }
        assert _filter_fields(data) == data
