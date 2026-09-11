__all__ = ()

import logging
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


logger = logging.getLogger(__name__)

FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
FALLBACK_FONT = "Helvetica"

_FONTS_READY = False
_FONTS_AVAILABLE = False


def _fonts_dir():
    """Папка со TTF-шрифтами. Перебираем варианты."""
    candidates = [
        Path(settings.BASE_DIR) / "static_dev" / "fonts",
        Path(settings.BASE_DIR) / "static" / "fonts",
        Path(settings.BASE_DIR) / "assets" / "fonts",
    ]
    for path in candidates:
        if path.exists():
            return path
    # если ни одной папки нет — вернём первую, чтобы warning был понятным
    return candidates[0]


def _register_fonts():
    """Регистрирует DejaVu. Возвращает True, если получилось."""
    global _FONTS_READY, _FONTS_AVAILABLE
    if _FONTS_READY:
        return _FONTS_AVAILABLE

    _FONTS_READY = True
    fonts_dir = _fonts_dir()
    regular = fonts_dir / "DejaVuSans.ttf"
    bold = fonts_dir / "DejaVuSans-Bold.ttf"

    logger.warning("Шрифты: ищу в %s", fonts_dir)
    logger.warning("  DejaVuSans.ttf      exists: %s", regular.exists())
    logger.warning("  DejaVuSans-Bold.ttf exists: %s", bold.exists())

    if not regular.exists():
        logger.warning(
            "Не найден %s — PDF будет с латинским дефолтом (кириллица сломается)",
            regular,
        )
        _FONTS_AVAILABLE = False
        return False

    try:
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
        if bold.exists():
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
        else:
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(regular)))
        _FONTS_AVAILABLE = True
    except Exception:
        logger.exception("Не удалось зарегистрировать шрифты")
        _FONTS_AVAILABLE = False

    return _FONTS_AVAILABLE


def _wrap_text(text, max_chars):
    lines = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        while len(raw) > max_chars:
            cut = raw.rfind(" ", 0, max_chars)
            if cut == -1:
                cut = max_chars
            lines.append(raw[:cut])
            raw = raw[cut:].lstrip()
        lines.append(raw)
    return lines


def generate_pdf(document):
    """Возвращает BytesIO с готовым PDF."""
    has_cyrillic_font = _register_fonts()

    rules = document.template.rules or {}
    font_size = rules.get("font", {}).get("size", 12)
    margins = rules.get("page", {}).get("margins", {})
    top = margins.get("top", 2) * cm
    bottom = margins.get("bottom", 2) * cm
    left = margins.get("left", 3) * cm
    right = margins.get("right", 1.5) * cm

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    x = left
    y = height - top
    line_height = font_size * 1.5
    max_chars = max(20, int((width - left - right) / (font_size * 0.55)))

    def new_page():
        nonlocal y
        c.showPage()
        y = height - top

    def draw_line(text="", bold=False):
        nonlocal y
        if y < bottom + line_height:
            new_page()
        if has_cyrillic_font:
            font = FONT_BOLD if bold else FONT_REGULAR
        else:
            font = FALLBACK_FONT
        c.setFont(font, font_size)
        c.drawString(x, y, text)
        y -= line_height

    extracted = document.extracted_fields or {}
    for rf in document.document_type.required_fields.all():
        value = extracted.get(rf.code) or rf.placeholder
        draw_line(f"{rf.label}: {value}")

    y -= line_height * 0.5

    for block in (document.processed_text or "").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in _wrap_text(block, max_chars):
            draw_line(line)
        y -= line_height * 0.5

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer