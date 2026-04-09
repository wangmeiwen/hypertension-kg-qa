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
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
from langchain_docling import DoclingLoader

# GLM-OCR 客户端按需加载


class _PdfContainsImagesError(RuntimeError):
    """用于标记 PDF 含图片，需要从 PyMuPDF 回退到 Docling。"""


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

    def __init__(self, enable_image_ocr: bool = True):
        """
        初始化 Docling 解析器。

        Args:
            enable_image_ocr: 是否启用图片 OCR 功能，默认为 True
        """
        self.enable_image_ocr = enable_image_ocr
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
        repo_root = Path(__file__).resolve().parents[2]
        repo_root_str = str(repo_root)
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)
        return repo_root

    def _ensure_docling_plugin_ready(self) -> None:
        self._ensure_repo_root_on_sys_path()
        entry_points = importlib.metadata.entry_points(group="docling")
        if not any(ep.name == DOCLING_GLM_PLUGIN_ENTRYPOINT for ep in entry_points):
            raise RuntimeError(
                f"Docling 外部 OCR 插件未找到: {DOCLING_GLM_PLUGIN_ENTRYPOINT}"
            )

    def _build_pdf_pipeline_options(self) -> ThreadedPdfPipelineOptions:
        from app.document_parser.docling_glm_ocr_plugin import GlmVisionOcrOptions

        return ThreadedPdfPipelineOptions(
            allow_external_plugins=True,
            do_ocr=self.enable_image_ocr,
            ocr_options=GlmVisionOcrOptions(),
        )

    def _build_converter(self) -> DocumentConverter:
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
        result = self.converter.convert(str(file_path))
        return self._convert_result(result, str(file_path))

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
        
        # PDF 文件优先使用 PyMuPDF 直接解析
        if suffix == ".pdf":
            try:
                return self._parse_pdf_with_pymupdf(file_bytes, filename)
            except _PdfContainsImagesError as e:
                logger.info("PDF 检测到图片，切换到 Docling 解析: %s", e)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
                logger.warning("PyMuPDF 解析失败，回退到 Docling: %s", e)
        
        # 其他格式使用 Docling
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        
        try:
            result = self.converter.convert(tmp_path)
            return self._convert_result(result, filename)
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    
    def _parse_pdf_with_pymupdf(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        """
        使用 PyMuPDF 直接解析 PDF。

        Args:
            file_bytes: PDF 文件字节内容
            filename: 文件名

        Returns:
            解析后的文档数据
        """
        import fitz  # PyMuPDF
        
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        
        try:
            pages = []
            text_parts = []
            markdown_parts = []
            
            for page_num in range(len(doc)):
                page = doc[page_num]

                # 一旦检测到图片，立即退出 PyMuPDF 流程并回退到 Docling。
                if page.get_images(full=True):
                    raise _PdfContainsImagesError(f"第 {page_num + 1} 页检测到图片")
                
                # 获取页面文本
                page_text = page.get_text("text")
                text_parts.append(page_text)
                
                # 简单拼接 Markdown 预览
                markdown_parts.append(f"## 第 {page_num + 1} 页\n\n{page_text}")
                
                # 页面信息
                pages.append({
                    "page_no": page_num + 1,
                    "size": [page.rect.width, page.rect.height],
                    "cells": 0,
                    "items": 0
                })
            
            # 合并文本
            text = "\n\n".join(text_parts)
            markdown = "\n\n".join(markdown_parts)
            
            # 元数据
            metadata = {
                "filename": filename,
                "mimetype": "application/pdf",
                "binary_hash": None,
                "num_pages": len(doc),
                "num_tables": 0,
                "num_images": 0,
                "ocr_engine": "pymupdf_backend_text",
                "picture_ocr_enabled": False,
                "docling_external_plugins_enabled": False,
            }
            
            return ParsedDocument(
                source=filename,
                format="pdf",
                markdown=markdown,
                text=text,
                pages=pages,
                tables=[],
                images=[],
                metadata=metadata
            )
        finally:
            doc.close()
    
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

    def _convert_result(self, result: ConversionResult, source: str) -> ParsedDocument:
        """
        转换 Docling 结果为标准格式，并对图片进行 OCR 识别。

        Args:
            result: Docling 转换结果
            source: 文档来源

        Returns:
            解析后的文档数据
        """
        doc = result.document
        
        # 导出为 Markdown
        markdown = doc.export_to_markdown()
        
        # 导出为纯文本
        text = doc.export_to_text()
        
        # 提取页面信息
        pages = []
        for page in doc.pages.values():
            pages.append({
                "page_no": page.page_no,
                "size": page.size.as_tuple() if page.size else None,
                "cells": len(page.cells) if hasattr(page, 'cells') and page.cells else 0,
                "items": len(page.items) if hasattr(page, 'items') and page.items else 0
            })
        
        # 提取表格信息
        tables = []
        for table in doc.tables:
            caption_obj = getattr(table, "caption", None)
            caption_text = getattr(caption_obj, "text", None) if caption_obj else None
            tables.append({
                "caption": caption_text,
                "data": table.export_to_dataframe().to_dict() if table.export_to_dataframe() is not None else None
            })
        
        # 提取图片信息并进行OCR识别
        images = []
        ocr_texts = []  # 存储所有图片的OCR结果
        
        for idx, picture in enumerate(doc.pictures):
            picture_caption = getattr(picture, "caption", None)
            picture_caption_text = getattr(picture_caption, "text", None) if picture_caption else None
            picture_annotations = getattr(picture, "annotations", None) or []
            image_info = {
                "caption": picture_caption_text,
                "annotations": [
                    {
                        "kind": getattr(ann, "kind", ""),
                        "text": getattr(ann, "text", "")
                    } for ann in picture_annotations
                ] if picture_annotations else []
            }
            
            # 如果启用了OCR，尝试识别图片中的文字
            if self.enable_image_ocr and self.ocr_client:
                try:
                    # 尝试获取图片的二进制数据
                    image_bytes = self._extract_image_bytes(picture)
                    if image_bytes:
                        logger.info("正在对第 %d 张图片进行OCR识别...", idx + 1)
                        ocr_result = self.ocr_client.recognize_image_bytes(image_bytes)
                        ocr_text = ocr_result.get("text", "")
                        image_info["ocr_text"] = ocr_text
                        image_info["ocr_source"] = "glm_docling_picture_pass"
                        if ocr_text:
                            ocr_texts.append(f"[图片{idx + 1} OCR结果]:\n{ocr_text}")
                            logger.info("第 %d 张图片OCR识别完成", idx + 1)
                except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
                    logger.error("第 %d 张图片OCR识别失败: %s", idx + 1, e)
                    image_info["ocr_error"] = str(e)
            
            images.append(image_info)
        
        # 如果有OCR结果，追加到文本中
        if ocr_texts:
            ocr_section = "\n\n" + "=" * 50 + "\n[图片OCR识别内容]\n" + "=" * 50 + "\n\n"
            ocr_section += "\n\n".join(ocr_texts)
            text += ocr_section
            markdown += "\n\n## 图片OCR识别内容\n\n" + "\n\n".join([
                f"### 图片{i+1}\n{t.replace(f'[图片{i+1} OCR结果]:' + chr(10), '')}" 
                for i, t in enumerate(ocr_texts)
            ])
        
        # 元数据
        metadata = {
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
    
    def _extract_image_bytes(self, picture) -> Optional[bytes]:
        """
        从 Docling picture 对象中提取图片二进制数据。

        Args:
            picture: Docling 的 Picture 对象

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
            logger.error("提取图片数据失败: %s", e)
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


