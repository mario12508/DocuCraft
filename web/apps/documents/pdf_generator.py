__all__ = ()

import logging
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

# Метрически совместимые замены для популярных шрифтов.
# Times New Roman / Calibri — коммерческие, TTF в комплект не входят.
# Liberation Serif ≈ Times New Roman, Liberation Sans ≈ Arial,
# Carlito ≈ Calibri. Скачайте ttf-файлы и положите в static_dev/fonts/.
FONT_ALIASES = {
    "times new roman": "LiberationSerif",
    "arial": "LiberationSans",
    "calibri": "Carlito",
    "dejavu sans": "DejaVuSans",
}

FALLBACK_FONT = "Helvetica"

_FONTS_READY = False
_REGISTERED = {}  # alias → реальное имя зарегистрированного шрифта


def _fonts_dir():
    for cand in (
            Path(settings.BASE_DIR) / "static_dev" / "fonts",
            Path(settings.BASE_DIR) / "static" / "fonts",
    ):
        if cand.exists():
            return cand
    return Path(settings.BASE_DIR) / "static_dev" / "fonts"


def _register_fonts():
    global _FONTS_READY
    if _FONTS_READY:
        return
    _FONTS_READY = True

    d = _fonts_dir()
    candidates = {
        "DejaVuSans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
        "LiberationSerif": (
        "LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
        "LiberationSans": (
        "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
        "Carlito": ("Carlito-Regular.ttf", "Carlito-Bold.ttf"),
    }
    for name, (reg, bold) in candidates.items():
        reg_path = d / reg
        if not reg_path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(reg_path)))
            bold_path = d / bold
            pdfmetrics.registerFont(
                TTFont(f"{name}-Bold",
                       str(bold_path if bold_path.exists() else reg_path))
            )
            _REGISTERED[name] = name
            logger.info("PDF: зарегистрирован %s", name)
        except Exception:
            logger.exception("PDF: не удалось зарегистрировать %s", name)


def _resolve_font(font_name):
    """Возвращает (regular, bold) из зарегистрированных или fallback."""
    if not _REGISTERED:
        return FALLBACK_FONT, FALLBACK_FONT
    key = (font_name or "").strip().lower()
    alias = FONT_ALIASES.get(key)
    if alias and alias in _REGISTERED:
        return alias, f"{alias}-Bold"
    # Первый зарегистрированный — как разумный дефолт
    first = next(iter(_REGISTERED))
    return first, f"{first}-Bold"


def _wrap(text, max_chars):
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
    _register_fonts()

    rules = document.template.rules or {}
    font_cfg = rules.get("font", {})
    font_name = font_cfg.get("name", "Times New Roman")
    font_size = font_cfg.get("size", 14)
    spacing = rules.get("line_spacing", 1.5)
    para = rules.get("paragraph", {})
    align = para.get("alignment", "justify")
    indent = para.get("first_line_indent", 1.25)
    layout = rules.get("layout", "classic")

    margins = rules.get("page", {}).get("margins", {})
    top = margins.get("top", 2) * cm
    bottom = margins.get("bottom", 2) * cm
    left = margins.get("left", 3) * cm
    right = margins.get("right", 1.5) * cm

    regular_font, bold_font = _resolve_font(font_name)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    x_left = left
    y = height - top
    line_height = font_size * spacing
    max_chars = max(20, int((width - left - right) / (font_size * 0.5)))
    indent_pt = indent * cm

    def new_page():
        nonlocal y
        c.showPage()
        y = height - top

    def draw(text="", bold=False, x=None, center=False, right_align=False):
        nonlocal y
        if y < bottom + line_height:
            new_page()
        f = bold_font if bold else regular_font
        c.setFont(f, font_size)
        text_width = c.stringWidth(text, f, font_size)

        if center:
            x_pos = (width - text_width) / 2
        elif right_align:
            x_pos = width - right - text_width
        elif x is not None:
            x_pos = x
        else:
            x_pos = x_left

        c.drawString(x_pos, y, text)
        y -= line_height

    extracted = document.extracted_fields or {}
    fields = {rf.code: rf for rf in
              document.document_type.required_fields.all()}

    # === Шапка ===
    if layout == "modern":
        subject = extracted.get("subject")
        if subject:
            draw(subject, bold=True)

        addressee = extracted.get("addressee") or "—"
        sender = extracted.get("sender") or "—"
        draw(f"Кому: {addressee}", bold=True)
        draw(sender)
        y -= line_height * 0.3

        date = extracted.get("date", "")
        number = extracted.get("number", "")
        if date or number:
            parts = []
            if date:
                parts.append(f"Дата: {date}")
            if number:
                parts.append(f"Номер: {number}")
            draw("   ".join(parts))
    else:  # classic
        addressee = extracted.get("addressee")
        if addressee:
            for line in addressee.split("\n"):
                draw(line.strip(), right_align=True)

        date = extracted.get("date", "")
        number = extracted.get("number", "")
        if date or number:
            parts = []
            if date:
                parts.append(f"Дата: {date}")
            if number:
                parts.append(f"Номер: {number}")
            draw("   ".join(parts))

        subject = extracted.get("subject")
        if subject:
            draw(subject, bold=True, center=True)

    y -= line_height * 0.3

    # === Основной текст ===
    first_paragraph = True
    for block in (document.processed_text or "").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = _wrap(block, max_chars)
        for i, line in enumerate(lines):
            if layout == "classic":
                draw(line, x=x_left + (indent_pt if i == 0 else 0))
            else:
                draw(line)
        y -= line_height * 0.3
        first_paragraph = False

    # === Подпись ===
    signature = extracted.get("signature") or extracted.get("sender")
    if signature:
        y -= line_height * 0.3
        for line in signature.split("\n"):
            if layout == "modern":
                draw(line.strip(), center=True)
            else:
                draw(line.strip())

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer