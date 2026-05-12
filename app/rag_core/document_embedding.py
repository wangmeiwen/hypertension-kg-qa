"""
文档嵌入模块。

本模块负责将分块后的文档内容转换为向量存储所需结构并写入 Milvus，支持：
- DOCX 子块嵌入
- Docling 子块嵌入
- 集合统计查询

主要类：
- DocumentEmbedder: 文档嵌入器，负责文档对象构建与向量写入

主要函数：
- embed_docx_file: 便捷的 DOCX 文件分块并嵌入入口
- embed_docx_bytes: 便捷的 DOCX 字节流分块并嵌入入口

"""

# ruff: noqa: E402

import os
import sys
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent.parent
src_path = current_dir.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from langchain_core.documents import Document

# 导入文档分块结果类型与分块入口
from app.document_parser.document_segmentation import DocumentSegmentation
from app.document_parser.docling_parser import ParsedDocument

# 导入配置
from app.core.config import Config

# 导入向量存储
from app.rag_core.vector_store import VectorStore

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DocumentEmbedder:
    """
    文档嵌入器。

    负责将分块结果转换为 LangChain Document 并写入向量库。
    """

    def __init__(self, vector_store: Optional[VectorStore] = None):
        """
        初始化文档嵌入器。

        Args:
            vector_store: 可选的向量存储实例，未传入时自动创建默认 VectorStore。

        Returns:
            None。
        """
        self.conf = Config()

        # 使用传入的vector_store或创建新的
        if vector_store:
            self.vector_store = vector_store
        else:
            from app.rag_core.runtime_resources import get_shared_vector_store
            self.vector_store = get_shared_vector_store(self.conf)

    def embed_docx_sub_chunks(self, sub_chunks: List[Dict], file_name: str = "") -> Dict:
        """
        嵌入 DOCX 子块结果。

        Args:
            sub_chunks: 子块数据列表。
            file_name: 文件名。

        Returns:
            Dict: 嵌入结果摘要。
        """
        documents = self._convert_docx_chunks_to_documents(sub_chunks)
        self.vector_store.add_documents(documents)
        return {
            "success": True,
            "file_name": file_name,
            "sub_chunks_count": len(sub_chunks),
            "message": f"DOCX 子块嵌入完成，共 {len(sub_chunks)} 个子块"
        }

    def embed_docling_sub_chunks(self, sub_chunks: List[Dict], file_name: str, parsed: ParsedDocument) -> Dict:
        """
        嵌入 Docling 子块结果。

        Args:
            sub_chunks: 子块数据列表。
            file_name: 文件名。
            parsed: Docling 解析结果对象。

        Returns:
            Dict: 嵌入结果摘要。
        """
        documents = self._convert_docling_sub_chunks_to_documents(sub_chunks, file_name, parsed)
        self.vector_store.add_documents(documents)
        return {
            "success": True,
            "file_name": file_name,
            "format": parsed.format,
            "pages": len(parsed.pages),
            "tables": len(parsed.tables),
            "images": len(parsed.images),
            "sub_chunks_count": len(sub_chunks),
            "message": f"Docling 子块嵌入完成，共 {len(sub_chunks)} 个子块"
        }

    def _convert_docx_chunks_to_documents(self, sub_chunks: List[Dict]) -> List[Document]:
        """
        将 DOCX 子块转换为 LangChain Document 对象。

        转换时会保留父块内容、页码、父子块索引和稳定 chunk_id，
        供后续检索、重排、引用溯源和前端展示使用。

        Args:
            sub_chunks: 子块数据列表。

        Returns:
            List[Document]: 可写入向量库的文档列表。
        """
        documents = []
        timestamp = datetime.now().isoformat()

        for chunk in sub_chunks:
            # 生成唯一ID
            unique_str = f"{chunk['source_doc']}_{chunk['parent_index']}_{chunk['sub_index']}_{chunk['text']}"
            chunk_id = hashlib.md5(unique_str.encode('utf-8')).hexdigest()

            # 构建元数据，包含页码信息
            metadata = {
                "parent_id": f"{chunk['source_doc']}_parent_{chunk['parent_index']}",
                "parent_content": chunk['parent_text'],
                "source": chunk['source_doc'],
                "timestamp": timestamp,
                "chunk_id": chunk_id,
                "parent_index": chunk['parent_index'],
                "sub_index": chunk['sub_index']
            }

            # 添加页码信息（如果存在）
            if chunk.get('page_num') is not None:
                metadata["page_num"] = chunk['page_num']
            if chunk.get('start_page') is not None:
                metadata["start_page"] = chunk['start_page']
            if chunk.get('end_page') is not None:
                metadata["end_page"] = chunk['end_page']

            doc = Document(
                page_content=chunk['text'],
                metadata=metadata
            )
            documents.append(doc)

        return documents

    def _convert_docling_sub_chunks_to_documents(self, sub_chunks: List[Dict], file_name: str, parsed: ParsedDocument) -> List[Document]:
        """
        将 Docling 子块转换为 LangChain Document 对象。

        Docling 路径会额外写入解析格式、页数和分块策略，
        便于检索结果区分传统 DOCX 分块与 Docling 解析来源。

        Args:
            sub_chunks: 子块数据列表。
            file_name: 文件名。
            parsed: Docling 解析结果对象。

        Returns:
            List[Document]: 可写入向量库的文档列表。
        """
        documents = []
        timestamp = datetime.now().isoformat()

        for i, chunk in enumerate(sub_chunks):
            unique_str = f"{file_name}_docling_{i}_{chunk['text']}"
            chunk_id = hashlib.md5(unique_str.encode('utf-8')).hexdigest()

            metadata = {
                "source": file_name,
                "timestamp": timestamp,
                "chunk_id": chunk_id,
                "chunk_index": i,
                "parent_index": chunk.get("parent_index", 0),
                "sub_index": chunk.get("sub_index", 0),
                "parent_content": chunk.get("parent_text", ""),
                "page_num": chunk.get("page_num", 0),
                "start_page": chunk.get("start_page", 0),
                "end_page": chunk.get("end_page", 0),
                "docling_format": parsed.format,
                "docling_pages": len(parsed.pages),
                "parser": "docling",
                "chunking_strategy": "hierarchical",
                "parent_chunk_size": self.conf.PARENT_CHUNK_SIZE,
                "sub_chunk_size": self.conf.SUB_CHUNK_SIZE
            }

            documents.append(Document(page_content=chunk["text"], metadata=metadata))

        return documents

    def get_collection_stats(self) -> Dict:
        """
        获取向量集合统计信息。

        Args:
            None。

        Returns:
            Dict: 集合统计结果。
        """
        try:
            stats = self.vector_store.client.get_collection_stats(
                collection_name=self.vector_store.collection_name
            )
            return {
                "success": True,
                "collection_name": self.vector_store.collection_name,
                "row_count": stats.get("row_count", 0)
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
            logger.error("获取集合统计信息失败: %s", e)
            return {
                "success": False,
                "error": str(e)
            }


# 便捷函数
def embed_docx_file(file_path: str, vector_store: Optional[VectorStore] = None) -> Dict:
    """
    便捷函数：DOCX 文件分块并嵌入。

    Args:
        file_path: DOCX 文件路径。
        vector_store: 可选向量存储实例。

    Returns:
        Dict: 嵌入结果摘要。
    """
    conf = Config()
    segmenter = DocumentSegmentation(
        parent_chunk_size=conf.PARENT_CHUNK_SIZE,
        sub_chunk_size=conf.SUB_CHUNK_SIZE,
        parent_chunk_overlap=conf.PARENT_CHUNK_OVERLAP,
        sub_chunk_overlap=conf.SUB_CHUNK_OVERLAP
    )
    hierarchical_chunks = segmenter.parse(file_path)
    sub_chunks = segmenter.get_all_sub_chunks(hierarchical_chunks)
    embedder = DocumentEmbedder(vector_store)
    embed_result = embedder.embed_docx_sub_chunks(sub_chunks, file_name=os.path.basename(file_path))
    embed_result["parent_chunks_count"] = len(hierarchical_chunks)
    return embed_result


def embed_docx_bytes(file_bytes: bytes, file_name: str = "uploaded_doc.docx", vector_store: Optional[VectorStore] = None) -> Dict:
    """
    便捷函数：DOCX 字节流分块并嵌入。

    Args:
        file_bytes: DOCX 字节流。
        file_name: 文件名。
        vector_store: 可选向量存储实例。

    Returns:
        Dict: 嵌入结果摘要。
    """
    conf = Config()
    segmenter = DocumentSegmentation(
        parent_chunk_size=conf.PARENT_CHUNK_SIZE,
        sub_chunk_size=conf.SUB_CHUNK_SIZE,
        parent_chunk_overlap=conf.PARENT_CHUNK_OVERLAP,
        sub_chunk_overlap=conf.SUB_CHUNK_OVERLAP
    )
    hierarchical_chunks = segmenter.parse_bytes(file_bytes, file_name)
    sub_chunks = segmenter.get_all_sub_chunks(hierarchical_chunks)
    embedder = DocumentEmbedder(vector_store)
    embed_result = embedder.embed_docx_sub_chunks(sub_chunks, file_name=file_name)
    embed_result["parent_chunks_count"] = len(hierarchical_chunks)
    return embed_result


