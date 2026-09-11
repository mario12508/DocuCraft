__all__ = ()

from io import BytesIO

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt


ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def _apply_page_rules(docx_doc, rules):
    page = rules.get("page", {})
    margins = page.get("margins", {})
    section = docx_doc.sections[0]
    section.top_margin = Cm(margins.get("top", 2))
    section.bottom_margin = Cm(margins.get("bottom", 2))
    section.left_margin = Cm(margins.get("left", 3))
    section.right_margin = Cm(margins.get("right", 1.5))


def _add_header_footer(docx_doc, rules):
    header_text = rules.get("header", "")
    if header_text:
        docx_doc.sections[0].header.paragraphs[0].text = header_text
    footer_text = rules.get("footer", "")
    if footer_text:
        docx_doc.sections[0].footer.paragraphs[0].text = footer_text


def _style_paragraph(paragraph, font_name, font_size, rules):
    for run in paragraph.runs:
        run.font.name = font_name
        run.font.size = Pt(font_size)

    align = rules.get("paragraph", {}).get("alignment", "justify")
    paragraph.alignment = ALIGN_MAP.get(align, WD_ALIGN_PARAGRAPH.JUSTIFY)

    pf = paragraph.paragraph_format
    pf.line_spacing = rules.get("line_spacing", 1.5)
    indent = rules.get("paragraph", {}).get("first_line_indent", 1.25)
    pf.first_line_indent = Cm(indent)


def generate_docx(document):
    """Возвращает BytesIO с готовым DOCX."""
    rules = document.template.rules or {}
    font = rules.get("font", {})
    font_name = font.get("name", "Times New Roman")
    font_size = font.get("size", 14)

    docx_doc = DocxDocument()
    _apply_page_rules(docx_doc, rules)
    _add_header_footer(docx_doc, rules)

    # 1) Реквизиты — всегда выводим все, у незаполненных — placeholder
    extracted = document.extracted_fields or {}
    for rf in document.document_type.required_fields.all():
        value = extracted.get(rf.code) or rf.placeholder
        p = docx_doc.add_paragraph(f"{rf.label}: {value}")
        _style_paragraph(p, font_name, font_size, rules)

    # 2) Отбивка
    docx_doc.add_paragraph()

    # 3) Основной текст
    for block in (document.processed_text or "").split("\n\n"):
        text = block.strip()
        if not text:
            continue
        p = docx_doc.add_paragraph(text)
        _style_paragraph(p, font_name, font_size, rules)

    buffer = BytesIO()
    docx_doc.save(buffer)
    buffer.seek(0)
    return buffer