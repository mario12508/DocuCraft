__all__ = ()

import logging
import tempfile
from io import BytesIO
from pathlib import Path

from apps.documents.docx_generator import generate_docx

logger = logging.getLogger(__name__)


class PDFError(Exception):
    """Ошибка конвертации DOCX в PDF."""


# Тексты, которые Spire.Doc добавляет в бесплатной версии.
SPIRE_WARNING_TEXTS = (
    "Evaluation Warning",
    "The document was created with Spire.Doc for Python",
    "Spire.Doc for Python",
)


def _strip_spire_warning(pdf_bytes: bytes) -> bytes:
    """
    Убирает водяной знак Spire.Doc из готового PDF.

    Использует PyMuPDF (fitz): находит текст warning по фрагментам
    и затирает его белым прямоугольником. Если PyMuPDF не установлен
    или что-то упало — возвращает PDF как есть.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning(
            "PyMuPDF не установлен — водяной знак Spire.Doc останется. "
            "Установите: pip install PyMuPDF"
        )
        return pdf_bytes

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        for page in doc:
            for text in SPIRE_WARNING_TEXTS:
                rects = page.search_for(text)
                for rect in rects:
                    expanded = fitz.Rect(
                        rect.x0 - 2,
                        rect.y0 - 2,
                        rect.x1 + 2,
                        rect.y1 + 2,
                    )
                    page.add_redact_annot(expanded, fill=(1, 1, 1))

            page.apply_redactions()

        output = BytesIO()
        doc.save(output)
        doc.close()
        output.seek(0)
        return output.read()

    except Exception:
        logger.exception("Не удалось убрать водяной знак Spire.Doc")
        return pdf_bytes


def _convert_spire(docx_path: Path, pdf_path: Path) -> None:
    """Конвертирует DOCX в PDF через Spire.Doc."""
    try:
        from spire.doc import Document as SpireDoc
    except ImportError:
        raise PDFError("Spire.Doc не установлен. Установите: pip install Spire.Doc")

    try:
        doc = SpireDoc()
        doc.LoadFromFile(str(docx_path))
        doc.SaveToFile(str(pdf_path))
        doc.Close()
    except Exception as exc:
        logger.exception("Spire.Doc: ошибка конвертации")
        raise PDFError(f"Spire.Doc: {exc}")


def generate_pdf(document) -> BytesIO:
    """
    Конвертирует DOCX в PDF через Spire.Doc,
    затем убирает водяной знак через PyMuPDF.

    Возвращает BytesIO с готовым PDF.
    """
    try:
        docx_buffer = generate_docx(document)
    except Exception as exc:
        logger.exception("Не удалось собрать DOCX")
        raise PDFError(f"Не удалось собрать DOCX: {exc}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        docx_path = tmp / f"document_{document.pk}.docx"
        pdf_path = tmp / f"document_{document.pk}.pdf"

        docx_path.write_bytes(docx_buffer.getvalue())

        _convert_spire(docx_path, pdf_path)

        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise PDFError("Spire.Doc создал пустой PDF.")

        pdf_bytes = pdf_path.read_bytes()

    pdf_bytes = _strip_spire_warning(pdf_bytes)

    buffer = BytesIO(pdf_bytes)
    buffer.seek(0)
    return buffer
