__all__ = ()

from io import BytesIO

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _apply_page_rules(docx_doc, rules):
    page = rules.get("page", {})
    margins = page.get("margins", {})
    section = docx_doc.sections[0]
    section.top_margin = Cm(margins.get("top", 2))
    section.bottom_margin = Cm(margins.get("bottom", 2))
    section.left_margin = Cm(margins.get("left", 3))
    section.right_margin = Cm(margins.get("right", 1.5))


def _add_header_footer(docx_doc, rules, document=None):
    section = docx_doc.sections[0]

    header_text = rules.get("header", "")
    if header_text:
        p = section.header.paragraphs[0]
        p.text = header_text
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        header_size = rules.get("header_font_size", 11)
        for run in p.runs:
            run.font.size = Pt(header_size)

    footer_template = rules.get("footer", "")
    if footer_template and document is not None:
        text = footer_template.format(
            document_type=document.document_type.name,
            date=(document.document_date.strftime("%d.%m.%Y")
                  if document.document_date else ""),
        )
        p = section.footer.paragraphs[0]
        p.text = text
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer_size = rules.get("footer_font_size", 10)
        for run in p.runs:
            run.font.size = Pt(footer_size)


def _style_run(run, font_name, font_size, bold=False):
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.font.bold = bold
    # Кириллица в Word — фиксируем шрифт для восточноазиатских/кириллических глифов
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), font_name)
    rfonts.set(qn("w:cs"), font_name)
    rfonts.set(qn("w:hAnsi"), font_name)
    rfonts.set(qn("w:ascii"), font_name)


def _style_paragraph(paragraph, font_name, font_size, rules,
                     align=None, first_line_indent=None, bold=False):
    for run in paragraph.runs:
        _style_run(run, font_name, font_size, bold=bold)

    if align is not None:
        paragraph.alignment = ALIGN_MAP.get(align, WD_ALIGN_PARAGRAPH.JUSTIFY)
    else:
        default_align = rules.get("paragraph", {}).get("alignment", "justify")
        paragraph.alignment = ALIGN_MAP.get(default_align,
                                            WD_ALIGN_PARAGRAPH.JUSTIFY)

    pf = paragraph.paragraph_format
    pf.line_spacing = rules.get("line_spacing", 1.5)
    if first_line_indent is not None:
        pf.first_line_indent = Cm(first_line_indent)
    else:
        pf.first_line_indent = Cm(
            rules.get("paragraph", {}).get("first_line_indent", 1.25)
        )


def _add_paragraph(docx_doc, text, rules, font_name, font_size,
                   align=None, first_line_indent=None, bold=False):
    if not text:
        return None
    p = docx_doc.add_paragraph(text)
    _style_paragraph(p, font_name, font_size, rules,
                     align=align, first_line_indent=first_line_indent,
                     bold=bold)
    return p


# ---------------------------------------------------------------------------
# Сборка блоков
# ---------------------------------------------------------------------------

def _emit_classic(docx_doc, document, rules, font_name, font_size):
    """Классический корпоративный: шапка справа, подпись слева."""
    fields = {rf.code: rf for rf in
              document.document_type.required_fields.all()}
    extracted = document.extracted_fields or {}

    # 1. Шапка справа: адресат (должность + ФИО)
    addressee = extracted.get("addressee")
    if addressee:
        for line in addressee.split("\n"):
            _add_paragraph(docx_doc, line.strip(), rules,
                           font_name, font_size,
                           align="right", first_line_indent=0)

    # 2. Дата и номер одной строкой слева
    date = extracted.get("date", "")
    number = extracted.get("number", "")
    if date or number:
        parts = []
        if date:
            parts.append(f"Дата: {date}")
        if number:
            parts.append(f"Номер: {number}")
        _add_paragraph(docx_doc, "   ".join(parts), rules,
                       font_name, font_size,
                       align="left", first_line_indent=0)

    # 3. Заголовок по центру жирным
    subject = extracted.get("subject")
    if subject:
        _add_paragraph(docx_doc, subject, rules, font_name, font_size,
                       align="center", first_line_indent=0, bold=True)

    # 4. Основной текст
    for block in (document.processed_text or "").split("\n\n"):
        text = block.strip()
        if not text:
            continue
        _add_paragraph(docx_doc, text, rules, font_name, font_size,
                       align="justify", first_line_indent=1.25)

    # 5. Подпись слева: должность + линия + ФИО
    sender = extracted.get("sender") or extracted.get("signature")
    if sender:
        for line in sender.split("\n"):
            _add_paragraph(docx_doc, line.strip(), rules,
                           font_name, font_size,
                           align="left", first_line_indent=0)


def _emit_modern(docx_doc, document, rules, font_name, font_size):
    """Современный регламентный: тема сверху, шапка таблицей, подпись по центру."""
    extracted = document.extracted_fields or {}

    # 1. Тема сверху жирным
    subject = extracted.get("subject")
    if subject:
        _add_paragraph(docx_doc, subject, rules, font_name, font_size,
                       align="left", first_line_indent=0, bold=True)

    # 2. Табличная шапка «Кому / От кого»
    addressee = extracted.get("addressee") or ""
    sender = extracted.get("sender") or ""
    if addressee or sender:
        table = docx_doc.add_table(rows=2, cols=2)
        table.autofit = True

        hdr_left = table.cell(0, 0).paragraphs[0]
        hdr_right = table.cell(0, 1).paragraphs[0]
        hdr_left.add_run("Кому:")
        hdr_right.add_run("От кого:")
        for p in (hdr_left, hdr_right):
            for r in p.runs:
                _style_run(r, font_name, font_size, bold=True)

        body_left = table.cell(1, 0).paragraphs[0]
        body_right = table.cell(1, 1).paragraphs[0]
        body_left.add_run(addressee)
        body_right.add_run(sender)
        for p in (body_left, body_right):
            for r in p.runs:
                _style_run(r, font_name, font_size)

        docx_doc.add_paragraph()

    # 3. Дата и номер
    date = extracted.get("date", "")
    number = extracted.get("number", "")
    if date or number:
        parts = []
        if date:
            parts.append(f"Дата: {date}")
        if number:
            parts.append(f"Номер: {number}")
        _add_paragraph(docx_doc, "   ".join(parts), rules,
                       font_name, font_size,
                       align="left", first_line_indent=0)

    # 4. Основной текст
    for block in (document.processed_text or "").split("\n\n"):
        text = block.strip()
        if not text:
            continue
        _add_paragraph(docx_doc, text, rules, font_name, font_size,
                       align="left", first_line_indent=0)

    # 5. Подпись по центру
    sender_for_sign = extracted.get("signature") or extracted.get("sender")
    if sender_for_sign:
        for line in sender_for_sign.split("\n"):
            _add_paragraph(docx_doc, line.strip(), rules,
                           font_name, font_size,
                           align="center", first_line_indent=0)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def generate_docx(document):
    """Возвращает BytesIO с готовым DOCX."""
    rules = document.template.rules or {}
    font = rules.get("font", {})
    font_name = font.get("name", "Times New Roman")
    font_size = font.get("size", 14)
    layout = rules.get("layout", "classic")

    docx_doc = DocxDocument()
    _apply_page_rules(docx_doc, rules)
    _add_header_footer(docx_doc, rules, document=document)

    if layout == "modern":
        _emit_modern(docx_doc, document, rules, font_name, font_size)
    else:
        _emit_classic(docx_doc, document, rules, font_name, font_size)

    buffer = BytesIO()
    docx_doc.save(buffer)
    buffer.seek(0)
    return buffer