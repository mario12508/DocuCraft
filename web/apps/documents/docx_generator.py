__all__ = ()

import logging
from io import BytesIO

from django.conf import settings
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

logger = logging.getLogger(__name__)

ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


# ---------------------------------------------------------------------------
# Замена плейсхолдеров в DOCX-шаблоне
# ---------------------------------------------------------------------------

def _collect_replacements(document):
    extracted = document.extracted_fields or {}

    def get(*codes, fallback=""):
        for c in codes:
            v = extracted.get(c)
            if v:
                return str(v)
        return fallback

    org = get("organization")
    addressee = get("addressee")
    sender = get("sender", "signature")

    return {
        "[Организация]": org,
        "[Название организации]": org,

        "[Кому]": addressee,
        "[Адресат]": addressee,
        "[Должность]": "",
        "[ФИО]": "",

        "[От кого]": sender,
        "[Автор]": sender,
        "[Отправитель]": sender,

        "[Дата]": get("date"),
        "[Номер]": get("number"),
        "[Заголовок]": get("subject"),
        "[Тема]": get("subject"),
        "[Текст документа]": document.processed_text or "",

        "[Подпись]": get("signature", "sender"),
        "[И.О. Фамилия]": get("author_name", "signature", "sender"),
    }


def _replace_in_paragraph(paragraph, replacements):
    """Заменяет плейсхолдеры в параграфе. Если значение пустое —
    удаляет всю строку."""
    if not paragraph.runs:
        return

    full_text = "".join(run.text for run in paragraph.runs)
    if not any(ph in full_text for ph in replacements):
        return

    new_text = full_text
    for ph, value in replacements.items():
        if ph not in new_text:
            continue
        if value == "":
            # Удаляем всю строку, если она состоит из одного плейсхолдера
            stripped = new_text.strip()
            if stripped == ph or stripped == ph.rstrip(":"):
                new_text = ""
                break
            # Иначе просто убираем плейсхолдер
            new_text = new_text.replace(ph, "")
        else:
            new_text = new_text.replace(ph, value)

    first_run = paragraph.runs[0]
    first_run.text = new_text
    for run in paragraph.runs[1:]:
        run.text = ""


def _replace_in_table(table, replacements):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                _replace_in_paragraph(p, replacements)
            for nested in cell.tables:
                _replace_in_table(nested, replacements)


def _render_from_file(document):
    tmpl = document.template
    if not tmpl.docx_template:
        return None

    tmpl.docx_template.open("rb")
    try:
        docx_doc = DocxDocument(tmpl.docx_template)
    finally:
        tmpl.docx_template.close()

    replacements = _collect_replacements(document)

    for p in docx_doc.paragraphs:
        _replace_in_paragraph(p, replacements)
    for table in docx_doc.tables:
        _replace_in_table(table, replacements)
    for section in docx_doc.sections:
        for p in section.header.paragraphs:
            _replace_in_paragraph(p, replacements)
        for p in section.footer.paragraphs:
            _replace_in_paragraph(p, replacements)

    # <<< НОВОЕ: заполняем колонтитулы из rules, если в шаблоне пусто >>>
    _apply_header_footer_from_rules(
        docx_doc, document.template.rules or {}, document
    )

    buffer = BytesIO()
    docx_doc.save(buffer)
    buffer.seek(0)
    return buffer


def _add_header_footer(docx_doc, rules, document=None):
    section = docx_doc.sections[0]

    # Верхний колонтитул — организация
    org_name = rules.get("organization", "ООО «Ромашка»")
    if org_name and not section.header.paragraphs[0].text.strip():
        p = section.header.paragraphs[0]
        p.text = org_name
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.font.size = Pt(rules.get("header_font_size", 11))

    # Нижний колонтитул — тип и дата
    footer_template = rules.get("footer", "")
    if footer_template and document is not None:
        text = footer_template.format(
            document_type=document.document_type.name,
            date=(document.document_date.strftime("%d.%m.%Y")
                  if document.document_date else ""),
        )
        if not section.footer.paragraphs[0].text.strip():
            p = section.footer.paragraphs[0]
            p.text = text
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER


# ---------------------------------------------------------------------------
# Программная сборка (fallback, если файл-шаблон не задан)
# ---------------------------------------------------------------------------

def _apply_page_rules(docx_doc, rules):
    page = rules.get("page", {})
    margins = page.get("margins", {})
    section = docx_doc.sections[0]
    section.top_margin = Cm(margins.get("top", 2))
    section.bottom_margin = Cm(margins.get("bottom", 2))
    section.left_margin = Cm(margins.get("left", 3))
    section.right_margin = Cm(margins.get("right", 1.5))


def _style_run(run, font_name, font_size, bold=False):
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:eastAsia", "w:cs", "w:hAnsi", "w:ascii"):
        rfonts.set(qn(attr), font_name)


def _add_paragraph(docx_doc, text, rules, font_name, font_size,
                   align=None, first_line_indent=None, bold=False):
    if not text:
        return
    p = docx_doc.add_paragraph(text)
    for run in p.runs:
        _style_run(run, font_name, font_size, bold=bold)
    if align:
        p.alignment = ALIGN_MAP.get(align, WD_ALIGN_PARAGRAPH.JUSTIFY)
    pf = p.paragraph_format
    pf.line_spacing = rules.get("line_spacing", 1.5)
    if first_line_indent is not None:
        pf.first_line_indent = Cm(first_line_indent)


def _render_programmatic(document):
    rules = document.template.rules or {}
    font = rules.get("font", {})
    font_name = font.get("name", "Times New Roman")
    font_size = font.get("size", 14)
    layout = rules.get("layout", "classic")
    extracted = document.extracted_fields or {}

    docx_doc = DocxDocument()
    _apply_page_rules(docx_doc, rules)

    if layout == "modern":
        if extracted.get("subject"):
            _add_paragraph(docx_doc, extracted["subject"], rules,
                           font_name, font_size, align="left",
                           first_line_indent=0, bold=True)
    else:
        addressee = extracted.get("addressee", "")
        if addressee:
            for line in addressee.split("\n"):
                _add_paragraph(docx_doc, line.strip(), rules,
                               font_name, font_size,
                               align="right", first_line_indent=0)
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
        if extracted.get("subject"):
            _add_paragraph(docx_doc, extracted["subject"], rules,
                           font_name, font_size,
                           align="center", first_line_indent=0, bold=True)

    for block in (document.processed_text or "").split("\n\n"):
        text = block.strip()
        if not text:
            continue
        _add_paragraph(docx_doc, text, rules, font_name, font_size,
                       align="justify" if layout == "classic" else "left",
                       first_line_indent=1.25 if layout == "classic" else 0)

    sender = extracted.get("signature") or extracted.get("sender")
    if sender:
        for line in sender.split("\n"):
            _add_paragraph(docx_doc, line.strip(), rules,
                           font_name, font_size,
                           align="left" if layout == "classic" else "center",
                           first_line_indent=0)

    buffer = BytesIO()
    docx_doc.save(buffer)
    buffer.seek(0)
    return buffer


def _apply_header_footer_from_rules(docx_doc, rules, document=None):
    section = docx_doc.sections[0]

    org_name = rules.get("organization")
    if org_name and not section.header.paragraphs[0].text.strip():
        p = section.header.paragraphs[0]
        p.text = org_name
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.font.size = Pt(rules.get("header_font_size", 11))

    footer_template = rules.get("footer", "")
    if footer_template and document is not None:
        text = footer_template.format(
            document_type=document.document_type.name,
            date=(document.document_date.strftime("%d.%m.%Y")
                  if document.document_date else ""),
        )
        p = section.footer.paragraphs[0]
        if not p.text.strip():
            p.text = text
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.font.size = Pt(rules.get("footer_font_size", 10))

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def generate_docx(document):
    """Возвращает BytesIO с готовым DOCX."""
    from_file = _render_from_file(document)
    if from_file is not None:
        return from_file
    return _render_programmatic(document)

