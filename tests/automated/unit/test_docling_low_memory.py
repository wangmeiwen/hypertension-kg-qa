"""Docling 低内存解析配置测试。"""

import fitz

from app.backend.services.graph_import_parser import GraphImportParser
from app.document_parser.docling_parser import DoclingParser


def test_pdf_pipeline_uses_bounded_batches_and_queue() -> None:
    parser = DoclingParser.__new__(DoclingParser)
    parser.enable_image_ocr = False

    options = parser._build_pdf_pipeline_options()

    assert options.do_ocr is False
    assert options.allow_external_plugins is False
    assert options.ocr_batch_size == 1
    assert options.layout_batch_size == 1
    assert options.table_batch_size == 1
    assert options.queue_max_size == 2
    assert options.accelerator_options.num_threads == 2


def _build_pdf_bytes(page_texts: list[str]) -> bytes:
    document = fitz.open()
    try:
        for text in page_texts:
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        return document.tobytes()
    finally:
        document.close()


def test_pdf_with_text_layer_skips_external_image_ocr() -> None:
    pdf_bytes = _build_pdf_bytes(["a" * 80, "b" * 80])

    assert GraphImportParser._requires_image_ocr(pdf_bytes, ".pdf") is False


def test_scanned_pdf_enables_external_image_ocr() -> None:
    pdf_bytes = _build_pdf_bytes(["", ""])

    assert GraphImportParser._requires_image_ocr(pdf_bytes, ".pdf") is True
