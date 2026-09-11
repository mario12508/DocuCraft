__all__ = ()

import json
import logging
import re
import time
from dataclasses import dataclass, field

from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — эксперт по официально-деловому стилю русского языка.
Твоя задача — обработать черновой текст документа и выдать результат СТРОГО в формате JSON без markdown-разметки и вводных слов.

Правила:
1. Исправь орфографию, грамматику и стиль (официально-деловой).
2. Выдели известные реквизиты. Если реквизита нет в тексте — укажи null.
3. НЕ ВЫДУМЫВАЙ отсутствующие факты: имена, даты, должности, суммы, номера.
4. ИСКЛЮЧЕНИЕ — поле "topic": тему документа МОЖНО и НУЖНО формулировать
   из содержания (например, "О предоставлении ежегодного отпуска"),
   даже если в черновике её нет дословно. Тема — это краткое изложение сути,
   а не новый факт.
5. Формулировки приводи к официально-деловому стилю, но смысл сохраняй.

Формат ответа JSON:
{
  "addressee_position": "должность адресата (строка или null)",
  "addressee_name": "ФИО адресата (строка или null)",
  "author_position": "должность автора (строка или null)",
  "author_name": "ФИО автора (строка или null)",
  "date": "дата документа (строка или null)",
  "topic": "тема/заголовок (строка или null)",
  "body": "исправленный и улучшенный текст документа"
}"""


class AIError(Exception):
    """Все провайдеры недоступны или вернули некорректный ответ."""
    pass


@dataclass
class AIResult:
    processed_text: str = ""
    extracted_fields: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""


def _clean_json(raw: str) -> dict:
    """Извлекает JSON из ответа, даже если модель добавила markdown."""
    if not raw:
        raise ValueError("Пустой ответ модели")

    cleaned = re.sub(r'```(?:json)?\s*', '', raw, flags=re.IGNORECASE).strip()
    cleaned = cleaned.rstrip('`').strip()

    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(cleaned)


def _call_provider(provider: dict, source_text: str, doc_type: str) -> AIResult:
    """Один вызов одного провайдера. Бросает исключение при ошибке."""
    if not provider.get("api_key"):
        raise ValueError(f"Не задан ключ для {provider['name']}")

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        timeout=provider.get("timeout", 15),
    )

    kwargs = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Тип документа: {doc_type}\n"
                f"Черновик: {source_text}"
            )},
        ],
        "temperature": 0.1,
    }

    if provider.get("supports_json"):
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    raw = response.choices[0].message.content
    data = _clean_json(raw)

    return AIResult(
        processed_text=data.get("body") or source_text,
        extracted_fields={
            k: v for k, v in data.items()
            if k != "body" and v is not None
        },
        provider=provider["name"],
        model=provider["model"],
    )


def process_draft(source_text: str, doc_type_name: str) -> AIResult:
    """
    Пробует провайдеров по цепочке. Возвращает AIResult.
    Бросает AIError, если все упали или вышли за общий таймаут.
    """
    providers = getattr(settings, "AI_PROVIDERS", [])
    total_timeout = getattr(settings, "AI_TOTAL_TIMEOUT", 45)

    if not providers:
        raise AIError("Не настроены AI-провайдеры")

    start = time.monotonic()
    errors = []

    for provider in providers:
        elapsed = time.monotonic() - start
        if elapsed >= total_timeout:
            errors.append(f"Общий таймаут {total_timeout}с исчерпан")
            break

        try:
            logger.info("AI: пробуем %s (%s)", provider["name"], provider["model"])
            result = _call_provider(provider, source_text, doc_type_name)
            logger.info("AI: успех через %s за %.1fс", provider["name"],
                        time.monotonic() - start)
            return result
        except Exception as exc:
            logger.warning("AI: %s упал — %s", provider["name"], exc)
            errors.append(f"{provider['name']}: {exc}")
            continue

    raise AIError("Все ИИ-провайдеры недоступны. " + " | ".join(errors))
