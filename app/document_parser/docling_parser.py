"""
Docling 文档解析模块。

本模块基于 IBM Docling 提供文档解析功能，支持：
- PDF、DOCX、PPTX、HTML 等多种格式
- 文档布局、表格等内容的丰富统一表示
- 图片 OCR 文字识别（集成智谱 GLM-4V）

主要类：
- ParsedDocument: 解析后的文档数据结构
- DoclingParser: Docling 文档解析器

依赖：
- docling: IBM Docling 文档解析库
- langchain_docling: LangChain Docling 集成
- fitz (PyMuPDF): PDF 解析库
"""

import importlib.metadata
import io
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Callable
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
from langchain_docling import DoclingLoader

logger = logging.getLogger(__name__)

# GLM-OCR 客户端按需加载


DOCLING_GLM_PLUGIN_ENTRYPOINT = "hypertension_docling_glm"
DOCLING_GLM_OCR_KIND = "glm_vision"


@dataclass
class ParsedDocument:
    """
    解析后的文档数据结构。

    Attributes:
        source: 文档来源（文件路径或 URL）
        format: 文档格式
        markdown: Markdown 格式文本
        text: 纯文本内容
        pages: 页面信息列表
        tables: 表格信息列表
        images: 图片信息列表
        metadata: 文档元数据
    """
    source: str
    format: str
    markdown: str
    text: str
    pages: List[Dict[str, Any]]
    tables: List[Dict[str, Any]]
    images: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class DoclingParser:
    """
    Docling 文档解析器。

    支持 PDF、DOCX、PPTX、HTML 等多种格式文档解析。
    支持图片 OCR：当文档中包含图片时，会自动调用智谱 OCR 识别图片中的文字。

    Attributes:
        converter: Docling 文档转换器
        enable_image_ocr: 是否启用图片 OCR 功能
        ocr_client: OCR 客户端实例
    """

    def __init__(
        self,
        enable_image_ocr: bool = True,
        *,
        strict_image_ocr: bool = False,
        progress_callback: Optional[Callable[..., None]] = None,
    ):
        """
        初始化 Docling 解析器。

        Args:
            enable_image_ocr: 是否启用图片 OCR 功能，默认为 True
            strict_image_ocr: 是否在图片 OCR 失败时直接抛错。
            progress_callback: 可选进度回调，用于后台任务状态展示。

        Returns:
            None: 无返回值。
        """
        self.enable_image_ocr = enable_image_ocr
        self.strict_image_ocr = strict_image_ocr
        self.progress_callback = progress_callback
        self.ocr_client = None

        if enable_image_ocr:
            self._ensure_docling_plugin_ready()
            try:
                from app.document_parser.glm_ocr_client import get_glm_ocr_client

                self.ocr_client = get_glm_ocr_client()
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
                raise RuntimeError(f"GLM OCR 插件未就绪: 智谱OCR客户端初始化失败: {e}") from e

        self.converter = self._build_converter()
        logger.info(
            "DoclingParser initialized: ocr_engine=%s external_plugins=%s picture_ocr_enabled=%s",
            DOCLING_GLM_OCR_KIND if enable_image_ocr else "disabled",
            bool(enable_image_ocr),
            bool(enable_image_ocr and self.ocr_client),
        )

    def _ensure_repo_root_on_sys_path(self) -> Path:
        """确保仓库根目录位于 sys.path，便于 Docling 插件发现本地包。

        Returns:
            Path: 仓库根目录路径。
        """
        repo_root = Path(__file__).resolve().parents[2]
        repo_root_str = str(repo_root)
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)
        return repo_root

    def _ensure_docling_plugin_ready(self) -> None:
        """检查 Docling 外部 OCR 插件是否已安装并可被 entry point 发现。

        Returns:
            None: 无返回值。

        Raises:
            RuntimeError: 未发现 GLM OCR Docling 插件。
        """
        self._ensure_repo_root_on_sys_path()
        entry_points = importlib.metadata.entry_points(group="docling")
        if not any(ep.name == DOCLING_GLM_PLUGIN_ENTRYPOINT for ep in entry_points):
            raise RuntimeError(
                f"Docling 外部 OCR 插件未找到: {DOCLING_GLM_PLUGIN_ENTRYPOINT}"
            )

    def _build_pdf_pipeline_options(self) -> ThreadedPdfPipelineOptions:
        """构建启用 GLM OCR 插件的 PDF/Image pipeline 选项。

        Returns:
            ThreadedPdfPipelineOptions: Docling PDF pipeline 配置。
        """
        from app.document_parser.docling_glm_ocr_plugin import GlmVisionOcrOptions

        return ThreadedPdfPipelineOptions(
            allow_external_plugins=True,
            do_ocr=self.enable_image_ocr,
            ocr_options=GlmVisionOcrOptions(),
        )

    def _build_converter(self) -> DocumentConverter:
        """构建 Docling DocumentConverter。

        Returns:
            DocumentConverter: 启用或禁用外部 OCR 插件的转换器实例。
        """
        if not self.enable_image_ocr:
            return DocumentConverter()

        pdf_options = self._build_pdf_pipeline_options()
        image_options = self._build_pdf_pipeline_options()
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=image_options),
            }
        )

    def parse_file(self, file_path: Union[str, Path]) -> ParsedDocument:
        """
        解析本地文件。

        Args:
            file_path: 文件路径

        Returns:
            解析后的文档数据
        """
        file_path = Path(file_path)
        return self.parse_bytes(file_path.read_bytes(), file_path.name)

    def parse_bytes(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        """
        从字节流解析文档。

        Args:
            file_bytes: 文件字节内容
            filename: 文件名（用于识别格式）

        Returns:
            解析后的文档数据
        """
        suffix = Path(filename).suffix.lower()

        if suffix == ".pdf":
            return self._parse_pdf_with_docling_fallback(file_bytes, filename)

        # 其他格式使用 Docling
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            if self.progress_callback:
                self.progress_callback("enter", "parse_document", "Docling 正在解析文档结构")
            result = self.converter.convert(tmp_path, raises_on_error=False)
            return self._convert_result(result, filename)
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _parse_pdf_with_docling_fallback(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        """PDF 先走 Docling，失败页再降级到 PyMuPDF + 图片 OCR。"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            if self.progress_callback:
                self.progress_callback("enter", "parse_document", "Docling 正在解析 PDF")
            result = self.converter.convert(tmp_path, raises_on_error=False)
            status_name = self._get_conversion_status_name(result)

            if status_name == "success":
                return self._convert_result(
                    result,
                    filename,
                    metadata_overrides={
                        "pdf_primary_backend": "docling",
                        "pdf_fallback_used": False,
                        "pdf_fallback_reason": "",
                        "docling_pdf_status": status_name,
                        "docling_failed_pages": [],
                        "page_source_map": {str(page.page_no): "docling" for page in result.pages},
                    },
                )

            if self.progress_callback:
                self.progress_callback(
                    "heartbeat",
                    "parse_document",
                    "Docling 部分页渲染失败，正在降级补齐失败页" if status_name == "partial_success" else "Docling 渲染失败，正在降级到 PyMuPDF",
                )
            return self._merge_pdf_with_pymupdf_fallback(file_bytes, filename, result, status_name)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def parse_url(self, url: str) -> ParsedDocument:
        """
        解析远程 URL 文档。

        Args:
            url: 文档 URL

        Returns:
            解析后的文档数据
        """
        result = self.converter.convert(url)
        return self._convert_result(result, url)

    def _convert_result(
        self,
        result: ConversionResult,
        source: str,
        *,
        metadata_overrides: Optional[Dict[str, Any]] = None,
    ) -> ParsedDocument:
        """
        转换 Docling 结果为标准格式，并对图片进行 OCR 识别。

        Args:
            result: Docling 转换结果
            source: 文档来源
            metadata_overrides: 可选元数据覆盖项，用于记录 PDF 降级状态。

        Returns:
            解析后的文档数据
        """
        doc = result.document

        if self.progress_callback:
            self.progress_callback("heartbeat", "parse_document", "Docling 文档结构解析完成，正在整理正文")

        page_text_map = self._build_docling_page_text_map(doc)
        page_markdown_map = self._build_docling_page_markdown_map(doc)
        pages = self._build_docling_pages(doc)
        tables = self._extract_tables(doc)
        images, ocr_texts_by_page = self._extract_docling_images(doc)
        text, markdown = self._compose_document_from_page_maps(page_text_map, page_markdown_map, ocr_texts_by_page)

        # 元数据
        metadata: Dict[str, Any] = {
            "filename": doc.origin.filename if doc.origin else None,
            "mimetype": doc.origin.mimetype if doc.origin else None,
            "binary_hash": doc.origin.binary_hash if doc.origin else None,
            "num_pages": len(doc.pages),
            "num_tables": len(doc.tables),
            "num_images": len(doc.pictures),
            "ocr_engine": DOCLING_GLM_OCR_KIND if self.enable_image_ocr else "disabled",
            "picture_ocr_enabled": bool(self.enable_image_ocr and self.ocr_client),
            "docling_external_plugins_enabled": bool(self.enable_image_ocr),
        }
        if metadata_overrides:
            metadata.update(metadata_overrides)

        # 识别格式
        format_type = self._detect_format(source)

        return ParsedDocument(
            source=source,
            format=format_type,
            markdown=markdown,
            text=text,
            pages=pages,
            tables=tables,
            images=images,
            metadata=metadata
        )

    def _get_conversion_status_name(self, result: ConversionResult) -> str:
        """统一提取 ConversionResult 状态。

        Args:
            result: Docling 转换结果。

        Returns:
            str: 小写状态文本。
        """
        status = getattr(result, "status", None)
        if isinstance(status, ConversionStatus):
            return status.value.lower()
        if status is None:
            return "unknown"
        status_text = str(status)
        if "." in status_text:
            status_text = status_text.split(".")[-1]
        return status_text.lower()

    def _extract_failed_page_numbers(self, result: ConversionResult, total_pages: int) -> list[int]:
        """根据 Docling 返回结果推导失败页。

        Args:
            result: Docling 转换结果。
            total_pages: PDF 总页数。

        Returns:
            list[int]: 失败页页码列表。
        """
        success_pages = {int(page.page_no) for page in getattr(result, "pages", []) if getattr(page, "page_no", None)}
        failed_pages = sorted(page_no for page_no in range(1, total_pages + 1) if page_no not in success_pages)
        if failed_pages:
            return failed_pages

        page_numbers: set[int] = set()
        for error in getattr(result, "errors", []) or []:
            message = str(getattr(error, "error_message", "") or "")
            match = re.search(r"Page\s+(\d+)", message)
            if match:
                page_numbers.add(int(match.group(1)))
        return sorted(page_numbers)

    def _merge_pdf_with_pymupdf_fallback(
        self,
        file_bytes: bytes,
        filename: str,
        result: ConversionResult,
        status_name: str,
    ) -> ParsedDocument:
        """Docling 失败后，对失败页或整份 PDF 执行 PyMuPDF + 图片 OCR 降级。

        Args:
            file_bytes: PDF 文件二进制内容。
            filename: 原始文件名。
            result: Docling 部分成功或失败的转换结果。
            status_name: Docling 转换状态。

        Returns:
            ParsedDocument: 合并 Docling 成功页和 PyMuPDF 降级页后的文档。
        """
        import fitz  # type: ignore[import-untyped]

        pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            total_pages = len(pdf_doc)
            failed_page_numbers = self._extract_failed_page_numbers(result, total_pages)
            if status_name == "failure" and not failed_page_numbers:
                failed_page_numbers = list(range(1, total_pages + 1))

            doc = result.document
            success_page_numbers = {
                int(page.page_no)
                for page in getattr(result, "pages", [])
                if getattr(page, "page_no", None) and int(page.page_no) not in failed_page_numbers
            }
            page_text_map = self._build_docling_page_text_map(doc)
            page_markdown_map = self._build_docling_page_markdown_map(doc)
            page_items = {int(page["page_no"]): page for page in self._build_docling_pages(doc)}
            tables = self._extract_tables(doc)
            # 对于进入 PDF 降级路径的文档，不再对 Docling 的 picture 对象做二次图片提取/OCR。
            # 原因是这批 picture 对象本身就可能处于部分失败状态，继续强行提取会在严格模式下把整次降级补齐提前打断。
            # 在该路径下，仅对失败页使用 PyMuPDF 提取图片并执行 OCR；成功页沿用 Docling 已经给出的正文结果。
            docling_ocr_texts_by_page: Dict[int, List[str]] = {}
            images: List[Dict[str, Any]] = []
            page_source_map: dict[str, str] = {}

            for page_no in range(1, total_pages + 1):
                if page_no in success_page_numbers:
                    page_source_map[str(page_no)] = "docling"
                    continue

                if self.progress_callback:
                    self.progress_callback(
                        "heartbeat",
                        "parse_document",
                        f"Docling 渲染失败，正在降级补齐第 {page_no}/{total_pages} 页",
                    )
                fallback_payload = self._extract_pdf_page_with_pymupdf(pdf_doc, page_no, total_pages)
                page_text_map[page_no] = fallback_payload["text"]
                page_markdown_map[page_no] = fallback_payload["markdown"]
                page_items[page_no] = fallback_payload["page"]
                images.extend(fallback_payload["images"])
                if fallback_payload["ocr_texts"]:
                    docling_ocr_texts_by_page.setdefault(page_no, []).extend(fallback_payload["ocr_texts"])
                page_source_map[str(page_no)] = fallback_payload["page_source"]

            text, markdown = self._compose_document_from_page_maps(
                page_text_map,
                page_markdown_map,
                docling_ocr_texts_by_page,
            )
            pages = [page_items[page_no] for page_no in sorted(page_items.keys())]
            metadata = {
                "filename": filename,
                "mimetype": "application/pdf",
                "binary_hash": getattr(getattr(doc, "origin", None), "binary_hash", None),
                "num_pages": total_pages,
                "num_tables": len(tables),
                "num_images": len(images),
                "ocr_engine": DOCLING_GLM_OCR_KIND if self.enable_image_ocr else "disabled",
                "picture_ocr_enabled": bool(self.enable_image_ocr and self.ocr_client),
                "docling_external_plugins_enabled": bool(self.enable_image_ocr),
                "pdf_primary_backend": "docling",
                "pdf_fallback_used": True,
                "pdf_fallback_reason": "Docling 渲染失败后已对失败页降级到 PyMuPDF + 图片 OCR",
                "docling_pdf_status": status_name,
                "docling_failed_pages": failed_page_numbers,
                "page_source_map": page_source_map,
            }
            if self.progress_callback:
                self.progress_callback("complete", "parse_document", "PDF 页面补齐完成")
            return ParsedDocument(
                source=filename,
                format="pdf",
                markdown=markdown,
                text=text,
                pages=pages,
                tables=tables,
                images=images,
                metadata=metadata,
            )
        finally:
            pdf_doc.close()

    def _build_docling_page_text_map(self, doc) -> Dict[int, str]:
        """构建按页纯文本映射。"""
        page_text_map: Dict[int, str] = {}
        for page_no in sorted(doc.pages.keys()):
            page_text_map[int(page_no)] = doc.export_to_text(page_no=page_no)
        return page_text_map

    def _build_docling_page_markdown_map(self, doc) -> Dict[int, str]:
        """构建按页 Markdown 映射。"""
        page_markdown_map: Dict[int, str] = {}
        for page_no in sorted(doc.pages.keys()):
            page_markdown_map[int(page_no)] = doc.export_to_markdown(page_no=page_no)
        return page_markdown_map

    def _build_docling_pages(self, doc) -> List[Dict[str, Any]]:
        """提取页面信息。"""
        pages = []
        for page in doc.pages.values():
            pages.append(
                {
                    "page_no": page.page_no,
                    "size": page.size.as_tuple() if page.size else None,
                    "cells": len(page.cells) if hasattr(page, "cells") and page.cells else 0,
                    "items": len(page.items) if hasattr(page, "items") and page.items else 0,
                }
            )
        return pages

    def _extract_tables(self, doc) -> List[Dict[str, Any]]:
        """提取表格信息。"""
        tables = []
        for table in doc.tables:
            caption_obj = getattr(table, "caption", None)
            caption_text = getattr(caption_obj, "text", None) if caption_obj else None
            dataframe = table.export_to_dataframe()
            tables.append(
                {
                    "caption": caption_text,
                    "data": dataframe.to_dict() if dataframe is not None else None,
                }
            )
        return tables

    def _resolve_picture_page_no(self, picture) -> Optional[int]:
        """解析图片所属页码。"""
        for prov in getattr(picture, "prov", None) or []:
            page_no = getattr(prov, "page_no", None)
            if page_no:
                return int(page_no)
        return None

    def _extract_docling_images(self, doc) -> tuple[List[Dict[str, Any]], Dict[int, List[str]]]:
        """提取 Docling 图片信息并做 OCR。

        Args:
            doc: Docling 文档对象。

        Returns:
            tuple[List[Dict[str, Any]], Dict[int, List[str]]]: 图片元数据列表和按页聚合的 OCR 文本。
        """
        images: List[Dict[str, Any]] = []
        ocr_texts_by_page: Dict[int, List[str]] = {}
        total_pictures = len(doc.pictures)

        if total_pictures == 0 and self.progress_callback:
            self.progress_callback("complete", "parse_document", "文档正文解析完成")

        for idx, picture in enumerate(doc.pictures):
            page_no = self._resolve_picture_page_no(picture)
            picture_caption = getattr(picture, "caption", None)
            picture_caption_text = getattr(picture_caption, "text", None) if picture_caption else None
            picture_annotations = getattr(picture, "annotations", None) or []
            image_info = {
                "page_no": page_no,
                "caption": picture_caption_text,
                "annotations": [
                    {
                        "kind": getattr(ann, "kind", ""),
                        "text": getattr(ann, "text", ""),
                    }
                    for ann in picture_annotations
                ] if picture_annotations else [],
            }

            if self.enable_image_ocr and self.ocr_client:
                try:
                    if self.progress_callback:
                        if idx == 0:
                            self.progress_callback("complete", "parse_document", "文档正文解析完成")
                            self.progress_callback("enter", "ocr_images", f"正在对第 {idx + 1}/{total_pictures} 张图片进行 OCR")
                        else:
                            self.progress_callback("heartbeat", "ocr_images", f"正在对第 {idx + 1}/{total_pictures} 张图片进行 OCR")
                    image_bytes = self._extract_image_bytes(picture, doc)
                    if not image_bytes:
                        raise RuntimeError(f"第 {idx + 1} 张图片无法提取图像数据")
                    logger.info("正在对第 %d 张图片进行OCR识别...", idx + 1)
                    ocr_result = self.ocr_client.recognize_image_bytes(image_bytes)
                    ocr_text = ocr_result.get("text", "")
                    image_info["ocr_text"] = ocr_text
                    image_info["ocr_source"] = "glm_docling_picture_pass"
                    if ocr_text:
                        ocr_texts_by_page.setdefault(page_no or 0, []).append(f"[图片{idx + 1} OCR结果]:\n{ocr_text}")
                        logger.info("第 %d 张图片OCR识别完成", idx + 1)
                    if self.progress_callback:
                        self.progress_callback("heartbeat", "ocr_images", f"第 {idx + 1}/{total_pictures} 张图片 OCR 已完成")
                except Exception as e:
                    logger.warning("第 %d 张图片OCR识别失败: %s", idx + 1, e)
                    image_info["ocr_error"] = str(e)
                    if self.progress_callback:
                        self.progress_callback("heartbeat", "ocr_images", f"第 {idx + 1}/{total_pictures} 张图片 OCR 失败: {e}")
                    if self.strict_image_ocr:
                        raise RuntimeError(f"第 {idx + 1} 张图片 OCR 失败: {e}") from e

            images.append(image_info)

        if total_pictures > 0 and self.enable_image_ocr and self.ocr_client and self.progress_callback:
            self.progress_callback("complete", "ocr_images", "文档图片 OCR 完成")
        return images, ocr_texts_by_page

    def _compose_document_from_page_maps(
        self,
        page_text_map: Dict[int, str],
        page_markdown_map: Dict[int, str],
        ocr_texts_by_page: Dict[int, List[str]],
    ) -> tuple[str, str]:
        """按页拼装正文和 Markdown。

        Args:
            page_text_map: 按页纯文本映射。
            page_markdown_map: 按页 Markdown 映射。
            ocr_texts_by_page: 按页聚合的 OCR 文本。

        Returns:
            tuple[str, str]: 拼接后的纯文本和 Markdown。
        """
        ordered_pages = sorted(set(page_text_map.keys()) | set(page_markdown_map.keys()))
        text_parts: List[str] = []
        markdown_parts: List[str] = []

        for page_no in ordered_pages:
            page_text = (page_text_map.get(page_no) or "").strip()
            page_markdown = (page_markdown_map.get(page_no) or "").strip()
            page_ocr_texts = ocr_texts_by_page.get(page_no, [])
            if page_ocr_texts:
                ocr_section_text = "\n\n" + "\n\n".join(page_ocr_texts)
                page_text = f"{page_text}{ocr_section_text}".strip()
                ocr_markdown = "\n\n".join(
                    f"### 图片{index + 1}\n{text.replace(f'[图片{index + 1} OCR结果]:' + chr(10), '')}"
                    for index, text in enumerate(page_ocr_texts)
                )
                page_markdown = f"{page_markdown}\n\n## 第 {page_no} 页图片OCR识别内容\n\n{ocr_markdown}".strip()
            text_parts.append(page_text)
            markdown_parts.append(page_markdown)
        return "\n\n".join(part for part in text_parts if part), "\n\n".join(part for part in markdown_parts if part)

    def _extract_pdf_page_with_pymupdf(self, doc, page_no: int, total_pages: int) -> Dict[str, Any]:
        """使用 PyMuPDF 提取单页正文并对该页图片执行 OCR。

        Args:
            doc: PyMuPDF 文档对象。
            page_no: 页码，1 起始。
            total_pages: PDF 总页数。

        Returns:
            Dict[str, Any]: 单页文本、Markdown、页面信息、图片信息和 OCR 文本。
        """
        page = doc[page_no - 1]
        page_text = page.get_text("text") or ""
        page_images: List[Dict[str, Any]] = []
        ocr_texts: List[str] = []
        seen_xrefs: set[int] = set()
        raw_images = page.get_images(full=True)

        for index, image_meta in enumerate(raw_images, start=1):
            xref = int(image_meta[0])
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            image_info: Dict[str, Any] = {
                "page_no": page_no,
                "image_index": index,
                "xref": xref,
            }
            try:
                image_payload = doc.extract_image(xref)
                image_bytes = image_payload.get("image")
                image_info["ext"] = image_payload.get("ext")
                image_info["width"] = image_payload.get("width")
                image_info["height"] = image_payload.get("height")
                if not image_bytes:
                    raise RuntimeError(f"第 {page_no} 页第 {index} 张图片无法提取图像数据")
                if self.enable_image_ocr and self.ocr_client:
                    if self.progress_callback:
                        self.progress_callback("enter" if index == 1 else "heartbeat", "ocr_images", f"正在对第 {page_no}/{total_pages} 页第 {index} 张图片进行 OCR")
                    ocr_result = self.ocr_client.recognize_image_bytes(image_bytes)
                    ocr_text = ocr_result.get("text", "")
                    image_info["ocr_text"] = ocr_text
                    image_info["ocr_source"] = "glm_pymupdf_image_pass"
                    if ocr_text:
                        ocr_texts.append(f"[第 {page_no} 页图片{index} OCR结果]:\n{ocr_text}")
            except Exception as exc:
                image_info["ocr_error"] = str(exc)
                if self.strict_image_ocr:
                    raise RuntimeError(f"第 {page_no} 页第 {index} 张图片 OCR 失败: {exc}") from exc
            page_images.append(image_info)

        markdown = f"## 第 {page_no} 页\n\n{page_text}".strip()
        page_source = "pymupdf_fallback_with_image_ocr" if any(item.get("ocr_text") for item in page_images) else "pymupdf_fallback"
        return {
            "text": page_text,
            "markdown": markdown,
            "page": {
                "page_no": page_no,
                "size": [page.rect.width, page.rect.height],
                "cells": 0,
                "items": 0,
            },
            "images": page_images,
            "ocr_texts": ocr_texts,
            "page_source": page_source,
        }

    def _extract_image_bytes(self, picture, doc) -> Optional[bytes]:
        """
        从 Docling picture 对象中提取图片二进制数据。

        Args:
            picture: Docling 的 Picture 对象
            doc: 当前 DoclingDocument

        Returns:
            图片二进制数据，如果无法提取则返回 None
        """
        try:
            # 尝试从picture对象获取图像数据
            # Docling的Picture对象可能有不同的属性来获取图像

            # 方法1: 尝试获取image属性
            if hasattr(picture, 'image') and picture.image:
                img = picture.image
                if hasattr(img, 'to_bytes'):
                    return img.to_bytes()
                elif hasattr(img, 'data'):
                    return img.data

            # 方法2: 尝试获取bitmap或pixel数据
            if hasattr(picture, 'bitmap') and picture.bitmap:
                return picture.bitmap

            # 方法3: 尝试导出为PIL Image然后转为bytes
            if hasattr(picture, 'get_image'):
                pil_img = None
                try:
                    pil_img = picture.get_image(doc)
                except TypeError:
                    # 兼容旧版本 Docling 的无参签名
                    pil_img = picture.get_image()
                if pil_img:
                    img_buffer = io.BytesIO()
                    pil_img.save(img_buffer, format='PNG')
                    return img_buffer.getvalue()

            # 方法4: 尝试从document的原始二进制中提取
            if hasattr(picture, 'uri') and picture.uri:
                # 如果是本地文件，尝试读取
                if os.path.exists(picture.uri):
                    with open(picture.uri, 'rb') as f:
                        return f.read()

            logger.warning("无法从picture对象提取图像数据，可用属性: %s", dir(picture))
            return None

        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
            logger.warning("提取图片数据失败: %s", e)
            return None

    def _detect_format(self, source: str) -> str:
        """
        检测文档格式。

        Args:
            source: 文档来源路径

        Returns:
            文档格式标识
        """
        suffix = Path(source).suffix.lower()
        format_map = {
            ".pdf": "pdf",
            ".docx": "docx",
            ".doc": "doc",
            ".pptx": "pptx",
            ".ppt": "ppt",
            ".html": "html",
            ".htm": "html",
            ".txt": "txt",
            ".md": "markdown",
            ".jpg": "image",
            ".jpeg": "image",
            ".png": "image",
        }
        return format_map.get(suffix, "unknown")

    def to_langchain_documents(self, file_path: Union[str, Path]) -> List[Any]:
        """
        使用 LangChain DoclingLoader 加载文档。

        Args:
            file_path: 文件路径

        Returns:
            LangChain Document 列表
        """
        loader = DoclingLoader(file_path=[str(file_path)])
        return loader.load()


# 便捷函数
def parse_document(source: Union[str, bytes], filename: Optional[str] = None) -> ParsedDocument:
    """
    便捷函数：解析文档。

    Args:
        source: 文件路径、URL 或字节内容
        filename: 文件名（当 source 为 bytes 时需要）

    Returns:
        解析后的文档数据
    """
    parser = DoclingParser()

    if isinstance(source, bytes):
        if not filename:
            raise ValueError("当 source 为 bytes 时，必须提供 filename 参数")
        return parser.parse_bytes(source, filename)
    elif source.startswith("http://") or source.startswith("https://"):
        return parser.parse_url(source)
    else:
        return parser.parse_file(source)


def parse_to_markdown(source: Union[str, bytes], filename: Optional[str] = None) -> str:
    """
    便捷函数：解析文档并返回 Markdown。

    Args:
        source: 文件路径、URL 或字节内容
        filename: 文件名（当 source 为 bytes 时需要）

    Returns:
        Markdown 格式文本
    """
    result = parse_document(source, filename)
    return result.markdown


if __name__ == "__main__":
    # 测试代码
    import sys

    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        print(f"解析文件: {file_path}")

        result = parse_document(file_path)

        print(f"\n格式: {result.format}")
        print(f"页数: {len(result.pages)}")
        print(f"表格数: {len(result.tables)}")
        print(f"图片数: {len(result.images)}")
        print("\nMarkdown 预览 (前500字符):")
        print(result.markdown[:500])
    else:
        print("用法: python docling_parser.py <文件路径>")


