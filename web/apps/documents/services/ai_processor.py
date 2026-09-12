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
2. Приведи формулировки к официально-деловому стилю, сохранив смысл и факты.
3. НЕ ВЫДУМЫВАЙ отсутствующие факты: имена, даты, должности, суммы, номера,
   адреса, ссылки на документы. Если реквизита нет в тексте — укажи null.
4. Поле "topic" — тема/заголовок документа. Её МОЖНО и НУЖНО
   сформулировать из содержания (например, «О предоставлении ежегодного
   оплачиваемого отпуска»), даже если в черновике она не названа дословно.
   Тема — краткое изложение сути, а не новый факт.
5. Поле "date" — ДАТА ДОКУМЕНТА, то есть дата, с которой документ исходит.
   Даты событий, договоров, поставок, проверок — НЕ являются датой документа.
   Если явной даты документа нет — верни null. НЕ переноси в "date" даты
   из тела документа.
6. Поле "number" — регистрационный номер документа. Если в черновике есть
   номер формата «47-СЗ», «12-ДЗ», «88-П» — перенеси его сюда без изменений.
   Если номера нет — верни null.
7. "body" — это исправленный основной текст БЕЗ шапки, без блока
   реквизитов и без подписи. Только содержательная часть.
8. Даты в тексте приводи к формату ДД.ММ.ГГГГ.

Формат ответа JSON:
{
  "addressee_position": "должность адресата (строка или null)",
  "addressee_name": "ФИО адресата (строка или null)",
  "author_position": "должность автора/составителя (строка или null)",
  "author_name": "ФИО автора/составителя (строка или null)",
  "date": "дата документа в формате ДД.ММ.ГГГГ (строка или null)",
  "number": "регистрационный номер (строка или null)",
  "topic": "тема или заголовок документа (строка или null)",
  "body": "исправленный основной текст документа"
}

Пример 1:
Вход: «Здрасьте! Нам надо купить три компа для отдела аналитики,
потому что старые уже не работают. Вообщем, цена 180000 рублей.
Поставщик ТехноСнаб обещал привезти за 10 дней.»
Выход:
{
  "addressee_position": null,
  "addressee_name": null,
  "author_position": null,
  "author_name": null,
  "date": null,
  "number": null,
  "topic": "О закупке офисной техники для отдела аналитики",
  "body": "В связи с выходом из строя рабочих станций отдела аналитики
прошу рассмотреть возможность закупки трёх комплектов офисной техники.
Общая стоимость — 180 000 рублей. Поставщик — ООО «ТехноСнаб» — осуществит
поставку в течение десяти рабочих дней."
}

Пример 2:
Вход: «Кому: генеральному директору ООО Ромашка Иванову И.И.
От кого: начальник отдела аналитики Петров П.П.
Дата 12.03.2025 Номер 47-СЗ
Заголовок О закупке офисной техники
Прошу выделить средства на покупку трёх компьютеров...»
Выход:
{
  "addressee_position": "генеральному директору",
  "addressee_name": "Иванову И.И.",
  "author_position": "начальник отдела аналитики",
  "author_name": "Петров П.П.",
  "date": "12.03.2025",
  "number": "47-СЗ",
  "topic": "О закупке офисной техники",
  "body": "Прошу выделить средства на покупку трёх компьютеров..."
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

    cleaned = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    cleaned = cleaned.rstrip("`").strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(cleaned)


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
