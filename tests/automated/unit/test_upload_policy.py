from __future__ import annotations

from io import BytesIO
import zipfile

import fitz
from PIL import Image
import pytest

from app.backend.security.upload_policy import inspect_upload


def _zip_bytes(
    entries: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    try:
        for _ in range(page_count):
            document.new_page()
        return document.tobytes()
    finally:
        document.close()


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_policy_accepts_valid_pdf_ooxml_text_and_image() -> None:
    pdf = inspect_upload(
        content=_pdf_bytes(2),
        filename="guide.pdf",
        declared_mime="application/pdf",
        allowed_suffixes={".pdf"},
        max_bytes=1024 * 1024,
    )
    docx = inspect_upload(
        content=_zip_bytes(
            {
                "[Content_Types].xml": b"<Types/>",
                "word/document.xml": b"<document/>",
            }
        ),
        filename="guide.docx",
        declared_mime=(
            "application/vnd.openxmlformats-officedocument." "wordprocessingml.document"
        ),
        allowed_suffixes={".docx"},
        max_bytes=1024 * 1024,
    )
    text = inspect_upload(
        content="高血压指南".encode(),
        filename="guide.md",
        declared_mime="text/plain; charset=utf-8",
        allowed_suffixes={".md"},
        max_bytes=1024,
    )
    image = inspect_upload(
        content=_png_bytes(),
        filename="chart.png",
        declared_mime="image/png",
        allowed_suffixes={".png"},
        max_bytes=1024,
    )

    assert pdf.page_count == 2
    assert docx.archive_entries == 2
    assert text.detected_type == "text"
    assert image.detected_type == "image"


def test_upload_policy_rejects_mime_and_file_signature_mismatch() -> None:
    with pytest.raises(ValueError, match="upload_mime_mismatch"):
        inspect_upload(
            content=_png_bytes(),
            filename="chart.png",
            declared_mime="application/pdf",
            allowed_suffixes={".png"},
            max_bytes=1024,
        )

    with pytest.raises(ValueError, match="upload_file_signature_mismatch:pdf"):
        inspect_upload(
            content=b"not a pdf",
            filename="guide.pdf",
            declared_mime="application/pdf",
            allowed_suffixes={".pdf"},
            max_bytes=1024,
        )


def test_upload_policy_rejects_unsafe_or_fake_archives() -> None:
    with pytest.raises(ValueError, match="upload_archive_unsafe_path"):
        inspect_upload(
            content=_zip_bytes(
                {
                    "[Content_Types].xml": b"<Types/>",
                    "../word/document.xml": b"<document/>",
                }
            ),
            filename="guide.docx",
            declared_mime=None,
            allowed_suffixes={".docx"},
            max_bytes=1024 * 1024,
        )

    with pytest.raises(ValueError, match="upload_invalid_ooxml_container"):
        inspect_upload(
            content=_zip_bytes({"random.txt": b"not docx"}),
            filename="guide.docx",
            declared_mime=None,
            allowed_suffixes={".docx"},
            max_bytes=1024 * 1024,
        )


def test_upload_policy_rejects_high_compression_ratio() -> None:
    bomb = _zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"0" * (2 * 1024 * 1024),
        }
    )

    with pytest.raises(ValueError, match="upload_archive_compression_ratio_exceeded"):
        inspect_upload(
            content=bomb,
            filename="guide.docx",
            declared_mime=None,
            allowed_suffixes={".docx"},
            max_bytes=1024 * 1024,
        )


def test_upload_policy_rejects_nul_text_and_invalid_json() -> None:
    with pytest.raises(ValueError, match="upload_text_contains_nul"):
        inspect_upload(
            content=b"hello\x00world",
            filename="guide.txt",
            declared_mime="text/plain",
            allowed_suffixes={".txt"},
            max_bytes=1024,
        )
    with pytest.raises(ValueError, match="upload_invalid_json"):
        inspect_upload(
            content=b"{invalid}",
            filename="data.json",
            declared_mime="application/json",
            allowed_suffixes={".json"},
            max_bytes=1024,
        )
