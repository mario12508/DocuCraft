__all__ = ()

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

import requests
import urllib3
from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── GigaChat ─────────────────────────────────────────────────────────
GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://api.giga.chat/v1/chat/completions"
GIGACHAT_SCOPE = "GIGACHAT_API_PERS"


SYSTEM_PROMPT = """Ты — эксперт по официально-деловому стилю русского языка.
Твоя задача — обработать черновой текст документа и выдать результат
СТРОГО в формате JSON без markdown-разметки и вводных слов.

Правила:
1. Исправь орфографию, грамматику и пунктуацию.
2. Приведи формулировки к официально-деловому стилю, сохранив смысл.
3. КРИТИЧНО — НЕ ВЫДУМЫВАЙ факты. Бери значения ТОЛЬКО из черновика:
   - имена и ФИО
   - названия организаций
   - должности
   - даты
   - номера документов
   - суммы и цифры
   - адреса
   Если чего-то нет в черновике — верни null. Не подставляй примеры
   из этого промпта, не придумывай правдоподобные названия.
4. Расширяй и структурируй текст: разбивай на логические абзацы,
   добавляй деловые вводные обороты («В связи с…», «В рамках…»,
   «По итогам…»), поясняй цель действия. Объём итогового текста может
   быть на 30–50% больше исходного. НЕ добавляй новые факты.
5. Суммы и крупные числа дублируй словами в скобках:
   «180 000 (сто восемьдесят тысяч) рублей».
6. Поле "topic" — тема документа. Формулируется из содержания
   (например, «О необходимости закупки офисной техники»), даже если
   в черновике её нет дословно.
7. Поле "date" — ДАТА ДОКУМЕНТА, а не даты событий из текста.
   Если явной даты документа нет — верни null.
8. Поле "number" — регистрационный номер документа.
   Если в черновике есть номер формата «47-СЗ» или подобный — перенеси.
   Если номера нет — верни null.
9. "body" — исправленный основной текст БЕЗ шапки, реквизитов
   и подписи. Разбивай на абзацы через пустую строку (\\n\\n).

Адресат и автор раскладываются на отдельные поля:
- addressee_position — должность адресата в дательном падеже
- addressee_org — название организации адресата
- addressee_name — ФИО адресата
- author_position — должность автора
- author_org — название организации автора (если есть)
- author_name — ФИО автора

Формат ответа JSON:
{
  "addressee_position": "строка или null",
  "addressee_org": "строка или null",
  "addressee_name": "строка или null",
  "author_position": "строка или null",
  "author_org": "строка или null",
  "author_name": "строка или null",
  "date": "ДД.ММ.ГГГГ или null",
  "number": "строка или null",
  "topic": "строка или null",
  "body": "основной текст"
}

Пример структуры (значения — заглушки, не переносить!):
Вход: «Кому: [должность] [организация] [ФИО].
От кого: [должность] [ФИО].
Дата: [дата]. Номер: [номер].
Заголовок: [тема].
[Произвольный текст черновика.]»

Выход:
{
  "addressee_position": "[должность из входа]",
  "addressee_org": "[организация из входа]",
  "addressee_name": "[ФИО из входа]",
  "author_position": "[должность из входа]",
  "author_org": null,
  "author_name": "[ФИО из входа]",
  "date": "[дата из входа]",
  "number": "[номер из входа]",
  "topic": "[тема, сформулированная по смыслу]",
  "body": "[текст в деловом стиле]"
}

ВАЖНО: значения выше — это метки-заглушки. В реальном ответе
в каждом поле должно быть значение из конкретного черновика,
а не текст в квадратных скобках."""


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
    """
    Извлекает JSON из ответа модели. Устойчив к:
    - markdown-обёрткам ```json ... ```
    - вводному тексту до/после JSON
    - неэкранированным control-символам (переносы строк внутри строк)
    """
    if not raw:
        raise ValueError("Пустой ответ модели")

    cleaned = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    cleaned = cleaned.rstrip("`").strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    candidate = match.group(0) if match else cleaned

    # Строгий парсинг
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Мягкий парсинг — разрешает control-символы внутри строк
    try:
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        pass

    # Последний шанс: заменяем control-символы на пробелы
    sanitized = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", candidate
    )
    return json.loads(sanitized, strict=False)


def _clean_value(v):
    """
    Нормализует значение, пришедшее от модели.
    Возвращает строку или None — если значение «пустое».
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.lower() in ("null", "none", "нет данных", "нет", "-", "—"):
            return None
        return s
    if isinstance(v, (int, float, bool)):
        return str(v)
    return None


def _filter_fields(data: dict) -> dict:
    """Оставляет только непустые поля, кроме body."""
    result = {}
    for k, v in data.items():
        if k == "body":
            continue
        cleaned = _clean_value(v)
        if cleaned is not None:
            result[k] = cleaned
    return result


# =====================================================================
# OpenAI-совместимые провайдеры (Groq, Gemini, OpenRouter)
# =====================================================================
def _call_provider(provider: dict, source_text: str, doc_type: str) -> AIResult:
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
        processed_text=_clean_value(data.get("body")) or source_text,
        extracted_fields=_filter_fields(data),
        provider=provider["name"],
        model=provider["model"],
    )


# =====================================================================
# GigaChat (Сбер) — отдельная ветка, не через OpenAI SDK
# =====================================================================
def _get_gigachat_token(auth_key: str) -> str:
    """Обменивает Authorization key на access_token GigaChat."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {auth_key}",
    }
    data = {"scope": GIGACHAT_SCOPE}
    resp = requests.post(
        GIGACHAT_AUTH_URL,
        headers=headers,
        data=data,
        verify=False,
        timeout=15,
    )
    if not resp.ok:
        logger.error(
            "GigaChat OAuth failed: status=%s body=%s",
            resp.status_code, resp.text,
        )
        raise ValueError(f"GigaChat auth {resp.status_code}: {resp.text}")
    return resp.json()["access_token"]


def _call_gigachat(provider: dict, source_text: str, doc_type: str) -> AIResult:
    """Отдельная ветка для GigaChat."""
    auth_key = provider.get("api_key")
    if not auth_key:
        raise ValueError("Не задан ключ для GigaChat")

    token = _get_gigachat_token(auth_key)
    model = provider.get("model", "GigaChat-2")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Тип документа: {doc_type}\n"
                f"Черновик: {source_text}"
            )},
        ],
        "temperature": 0.1,
    }

    resp = requests.post(
        GIGACHAT_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        verify=False,
        timeout=provider.get("timeout", 30),
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"]
    data = _clean_json(raw)

    return AIResult(
        processed_text=_clean_value(data.get("body")) or source_text,
        extracted_fields=_filter_fields(data),
        provider=provider["name"],
        model=model,
    )


# =====================================================================
# Основная точка входа с fallback-цепочкой
# =====================================================================
def process_draft(source_text: str, doc_type_name: str) -> AIResult:
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
            logger.info(
                "AI: пробуем %s (%s)",
                provider["name"], provider.get("model", "?"),
            )

            if provider["name"] == "gigachat":
                result = _call_gigachat(provider, source_text, doc_type_name)
            else:
                result = _call_provider(provider, source_text, doc_type_name)

            logger.info(
                "AI: успех через %s за %.1fс",
                provider["name"], time.monotonic() - start,
            )
            return result

        except Exception as exc:
            logger.warning("AI: %s упал — %s", provider["name"], exc)
            errors.append(f"{provider['name']}: {exc}")
            continue

    raise AIError("Все ИИ-провайдеры недоступны. " + " | ".join(errors))


def pick_provider():
    providers = getattr(settings, "AI_PROVIDERS", [])
    for p in providers:
        if p.get("api_key"):
            return p
    return None


def pick_gigachat_provider():
    providers = getattr(settings, "AI_PROVIDERS", [])
    for p in providers:
        if p.get("name") == "gigachat" and p.get("api_key"):
            return p
    return None


def pick_openai_provider():
    providers = getattr(settings, "AI_PROVIDERS", [])
    for p in providers:
        if p.get("api_key") and p.get("base_url"):
            return p
    return None
