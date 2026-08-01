"""上传文件的扩展名、MIME、文件头和资源上限校验。"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import zipfile

DEFAULT_MAX_ARCHIVE_ENTRIES = 5000
DEFAULT_MAX_EXPANDED_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200
DEFAULT_MAX_PDF_PAGES = 1000
DEFAULT_MAX_IMAGE_PIXELS = 50_000_000

MIME_TYPES_BY_SUFFIX = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
    ".doc": {"application/msword", "application/x-ole-storage"},
    ".ppt": {"application/vnd.ms-powerpoint", "application/x-ole-storage"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "text/plain", "application/csv"},
    ".json": {"application/json", "text/json", "text/plain"},
    ".html": {"text/html"},
    ".htm": {"text/html"},
    ".epub": {"application/epub+zip", "application/zip"},
    ".mobi": {"application/x-mobipocket-ebook", "application/octet-stream"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".gif": {"image/gif"},
    ".bmp": {"image/bmp"},
}


@dataclass(frozen=True)
class UploadInspection:
    """上传校验后的只读元数据。"""

    suffix: str
    detected_type: str
    page_count: int = 0
    archive_entries: int = 0
    expanded_bytes: int = 0


def _validate_declared_mime(suffix: str, declared_mime: str | None) -> None:
    value = (declared_mime or "").split(";", 1)[0].strip().lower()
    if not value or value == "application/octet-stream":
        return
    expected = MIME_TYPES_BY_SUFFIX.get(suffix)
    if expected is not None and value not in expected:
        raise ValueError(f"upload_mime_mismatch:{suffix}:{value}")


def _inspect_zip(content: bytes, suffix: str) -> tuple[int, int]:
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("upload_invalid_zip_container") from exc
    with archive:
        members = archive.infolist()
        if len(members) > DEFAULT_MAX_ARCHIVE_ENTRIES:
            raise ValueError("upload_archive_too_many_entries")
        expanded_bytes = sum(max(0, member.file_size) for member in members)
        compressed_bytes = sum(max(0, member.compress_size) for member in members)
        if expanded_bytes > DEFAULT_MAX_EXPANDED_BYTES:
            raise ValueError("upload_archive_expanded_size_exceeded")
        if (
            expanded_bytes > 1024 * 1024
            and expanded_bytes
            > max(1, compressed_bytes) * DEFAULT_MAX_COMPRESSION_RATIO
        ):
            raise ValueError("upload_archive_compression_ratio_exceeded")
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("upload_archive_unsafe_path")
            if member.flag_bits & 0x1:
                raise ValueError("upload_archive_encrypted_entry")
        member_names = {member.filename.replace("\\", "/") for member in members}
        required_prefix = {
            ".docx": "word/",
            ".pptx": "ppt/",
            ".xlsx": "xl/",
        }.get(suffix)
        if required_prefix and (
            "[Content_Types].xml" not in member_names
            or not any(name.startswith(required_prefix) for name in member_names)
        ):
            raise ValueError(f"upload_invalid_ooxml_container:{suffix}")
        if suffix == ".epub":
            if "mimetype" not in member_names:
                raise ValueError("upload_invalid_epub_container")
            if archive.read("mimetype") != b"application/epub+zip":
                raise ValueError("upload_invalid_epub_mimetype")
        return len(members), expanded_bytes


def _inspect_pdf(content: bytes) -> int:
    if not content.startswith(b"%PDF-"):
        raise ValueError("upload_file_signature_mismatch:pdf")
    try:
        import fitz  # type: ignore[import-untyped]

        document = fitz.open(stream=content, filetype="pdf")
        try:
            page_count = len(document)
        finally:
            document.close()
    except Exception as exc:
        raise ValueError("upload_invalid_pdf") from exc
    if page_count > DEFAULT_MAX_PDF_PAGES:
        raise ValueError("upload_pdf_page_limit_exceeded")
    return page_count


def _inspect_image(content: bytes, suffix: str) -> None:
    try:
        from PIL import Image

        with Image.open(BytesIO(content)) as image:
            expected_format = {
                ".png": "PNG",
                ".jpg": "JPEG",
                ".jpeg": "JPEG",
                ".gif": "GIF",
                ".bmp": "BMP",
            }[suffix]
            if image.format != expected_format:
                raise ValueError(f"upload_file_signature_mismatch:{suffix[1:]}")
            width, height = image.size
            if width * height > DEFAULT_MAX_IMAGE_PIXELS:
                raise ValueError("upload_image_pixel_limit_exceeded")
            image.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("upload_invalid_image") from exc


def _inspect_text(content: bytes, suffix: str) -> None:
    if b"\x00" in content:
        raise ValueError("upload_text_contains_nul")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("upload_text_not_utf8") from exc
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("upload_invalid_json") from exc
    if suffix in {".html", ".htm"} and "<" not in text:
        raise ValueError("upload_invalid_html")


def inspect_upload(
    *,
    content: bytes,
    filename: str,
    declared_mime: str | None,
    allowed_suffixes: set[str] | frozenset[str],
    max_bytes: int,
) -> UploadInspection:
    """校验上传内容并返回资源规模元数据。"""
    if not filename:
        raise ValueError("upload_filename_required")
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"upload_suffix_not_allowed:{suffix}")
    if not content:
        raise ValueError("upload_file_empty")
    if len(content) > max_bytes:
        raise ValueError(f"upload_file_size_exceeded:{max_bytes}")
    _validate_declared_mime(suffix, declared_mime)

    if suffix == ".pdf":
        return UploadInspection(
            suffix=suffix,
            detected_type="pdf",
            page_count=_inspect_pdf(content),
        )
    if suffix in {".docx", ".pptx", ".xlsx", ".epub"}:
        entries, expanded_bytes = _inspect_zip(content, suffix)
        return UploadInspection(
            suffix=suffix,
            detected_type="zip_container",
            archive_entries=entries,
            expanded_bytes=expanded_bytes,
        )
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}:
        _inspect_image(content, suffix)
        return UploadInspection(suffix=suffix, detected_type="image")
    if suffix in {".txt", ".md", ".csv", ".json", ".html", ".htm"}:
        _inspect_text(content, suffix)
        return UploadInspection(suffix=suffix, detected_type="text")
    if suffix in {".doc", ".ppt"}:
        if not content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise ValueError("upload_file_signature_mismatch:ole")
        return UploadInspection(suffix=suffix, detected_type="ole")
    if suffix == ".mobi":
        if len(content) < 68 or content[60:68] != b"BOOKMOBI":
            raise ValueError("upload_file_signature_mismatch:mobi")
        return UploadInspection(suffix=suffix, detected_type="mobi")
    raise ValueError(f"upload_inspector_missing:{suffix}")
