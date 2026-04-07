"""
文档分块模块。

本模块统一负责文本分块与页码信息处理，支持：
- 基于解析结果的页码估算与映射
- 纯文本分块（如 Docling+OCR 输出）
- 层次化切块（父子块结构）
- 基于 HanLP 的句子切分

主要类：
- HierarchicalChunk: 层次化切块数据结构
- DocxParser: 文本分块与页码估算器
- DocumentSegmentation: 文档分块入口类

"""

import re
import math
import logging
from dataclasses import dataclass
from typing import List, Optional

from app.document_parser.docling_parser import DoclingParser

logger = logging.getLogger(__name__)


@dataclass
class HierarchicalChunk:
    """
    层次化切块数据结构。

    包含父块和子块的层次化文本切块结构，支持页码信息。

    Attributes:
        parent_text: 父块文本内容
        parent_index: 父块索引
        sub_chunks: 子块文本列表
        source_doc: 来源文档名称
        page_num: 主页面号
        start_page: 起始页码
        end_page: 结束页码
    """
    parent_text: str
    parent_index: int
    sub_chunks: List[str]
    source_doc: str
    page_num: Optional[int] = None
    start_page: Optional[int] = None
    end_page: Optional[int] = None


class DocxParser:
    """
    文档文本分块与页码估算器。

    提供句子切分和层次化切块功能，文档解析由 DoclingParser 负责。

    Attributes:
        parent_chunk_size: 父块大小（字符数）
        sub_chunk_size: 子块大小（字符数）
        parent_chunk_overlap: 父块重叠大小
        sub_chunk_overlap: 子块重叠大小
    """

    def __init__(
        self,
        parent_chunk_size: int = 1200,
        sub_chunk_size: int = 300,
        parent_chunk_overlap: int = 100,
        sub_chunk_overlap: int = 50
    ):
        """
        初始化 DOCX 解析器。

        Args:
            parent_chunk_size: 父块大小，默认 1200 字符
            sub_chunk_size: 子块大小，默认 300 字符
            parent_chunk_overlap: 父块重叠大小，默认 100 字符
            sub_chunk_overlap: 子块重叠大小，默认 50 字符
        """
        self.parent_chunk_size = parent_chunk_size
        self.sub_chunk_size = sub_chunk_size
        self.parent_chunk_overlap = parent_chunk_overlap
        self.sub_chunk_overlap = sub_chunk_overlap

        # 初始化HanLP（延迟加载）
        self._hanlp_tokenizer = None
    
    @property
    def hanlp_tokenizer(self):
        """
        延迟初始化 HanLP 分词器。

        Returns:
            HanLP 分词器实例
        """
        if self._hanlp_tokenizer is None:
            import hanlp
            # 使用HanLP的CTB9分词模型
            self._hanlp_tokenizer = hanlp.load(hanlp.pretrained.tok.CTB9_TOK_ELECTRA_BASE)
        return self._hanlp_tokenizer

    def parse_docx(self, file_path: str) -> str:
        """
        通过 Docling 解析 DOCX 并提取文本。

        Args:
            file_path: DOCX 文件路径

        Returns:
            提取的文本内容
        """
        parser = DoclingParser(enable_image_ocr=False)
        parsed = parser.parse_file(file_path)
        return str(parsed.text or "")

    def parse_docx_with_page_info(self, file_path: str) -> tuple:
        """
        通过 Docling 解析 DOCX，并生成段落页码映射。

        Args:
            file_path: DOCX 文件路径

        Returns:
            元组 (文本内容, 段落页码映射列表)，每个元素是 (段落文本, 估算页码)
        """
        parser = DoclingParser(enable_image_ocr=False)
        parsed = parser.parse_file(file_path)
        text = str(parsed.text or "")

        if not text.strip():
            return "", []

        total_pages = len(parsed.pages)
        chars_per_page = math.ceil(len(text) / total_pages) if total_pages > 0 else 1800
        chars_per_page = max(1, chars_per_page)

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        paragraphs_with_pages = []
        current_char_total = 0
        for para in paragraphs:
            page_no = (current_char_total // chars_per_page) + 1
            paragraphs_with_pages.append({"text": para, "page": page_no})
            current_char_total += len(para)

        full_text = "\n\n".join([str(p["text"]) for p in paragraphs_with_pages])
        return full_text, paragraphs_with_pages
    
    def parse_docx_bytes(self, file_bytes: bytes) -> str:
        """
        通过 Docling 从字节流解析 DOCX。

        Args:
            file_bytes: DOCX 文件字节数据

        Returns:
            提取的文本内容
        """
        parser = DoclingParser(enable_image_ocr=False)
        parsed = parser.parse_bytes(file_bytes, "uploaded_doc.docx")
        return str(parsed.text or "")

    def split_by_sentences(self, text: str) -> List[str]:
        """
        使用 HanLP 进行句子切分。

        Args:
            text: 待切分的文本

        Returns:
            句子列表
        """
        try:
            # 先按段落分割
            paragraphs = text.split('\n\n')
            sentences = []
            
            for para in paragraphs:
                if not para.strip():
                    continue
                
                # 使用HanLP进行分句
                para_sentences = self._split_paragraph_by_sentences(para)
                sentences.extend(para_sentences)
            
            return sentences
            
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            # 如果HanLP出错，使用简单的规则切分
            logger.warning(
                "hanlp_sentence_split_failed_fallback: text_len=%s error_type=%s error=%s",
                len(str(text or "")),
                type(exc).__name__,
                exc,
            )
            return self._fallback_sentence_split(text)
    
    def _split_paragraph_by_sentences(self, para: str) -> List[str]:
        """
        使用 HanLP 分词后按标点重组句子。

        Args:
            para: 段落文本

        Returns:
            句子列表
        """
        try:
            # 使用HanLP进行分词
            tokens = self.hanlp_tokenizer(para)
            
            sentences = []
            current_sentence = []
            
            # 句末标点符号
            sentence_endings = {'。', '！', '？', '.', '!', '?', '；', ';'}
            
            for token in tokens:
                current_sentence.append(token)
                # 遇到句末标点，保存当前句子
                if token in sentence_endings:
                    if current_sentence:
                        sentence = ''.join(current_sentence).strip()
                        if sentence:
                            sentences.append(sentence)
                        current_sentence = []
            
            # 处理最后剩余的文本
            if current_sentence:
                sentence = ''.join(current_sentence).strip()
                if sentence:
                    sentences.append(sentence)
            
            return sentences
            
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            # 降级处理
            logger.warning(
                "hanlp_paragraph_split_failed_fallback: para_len=%s error_type=%s error=%s",
                len(str(para or "")),
                type(exc).__name__,
                exc,
            )
            return self._fallback_sentence_split(para)
    
    def _fallback_sentence_split(self, text: str) -> List[str]:
        """
        备用的句子切分方法（基于正则）。

        Args:
            text: 待切分的文本

        Returns:
            句子列表
        """
        # 按段落分割
        paragraphs = text.split('\n\n')
        sentences = []
        
        # 句子分隔符正则
        sentence_pattern = r'([^。！？.!?；;]+[。！？.!?；;]*)'
        
        for para in paragraphs:
            if not para.strip():
                continue
            
            # 切分句子
            matches = re.findall(sentence_pattern, para.strip())
            for match in matches:
                sentence = match.strip()
                if sentence:
                    sentences.append(sentence)
            
            # 如果没有匹配到（可能没有标点），将整个段落作为一句
            if not matches and para.strip():
                sentences.append(para.strip())
        
        return sentences
    
    def create_parent_chunks(self, sentences: List[str]) -> List[str]:
        """
        创建父块。

        Args:
            sentences: 句子列表

        Returns:
            父块文本列表
        """
        if not sentences:
            return []
        
        parent_chunks = []
        current_chunk: List[str] = []
        current_length = 0
        
        for sentence in sentences:
            sentence_length = len(sentence)
            
            # 如果当前句子本身就超过父块大小，直接作为一个块
            if sentence_length >= self.parent_chunk_size:
                # 先保存之前的累积内容
                if current_chunk:
                    parent_chunks.append(''.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                # 将长句子作为一个独立块
                parent_chunks.append(sentence)
                continue
            
            # 检查加入当前句子后是否会超过限制
            if current_length + sentence_length > self.parent_chunk_size and current_chunk:
                # 保存当前块
                parent_chunks.append(''.join(current_chunk))
                
                # 保留重叠部分
                overlap_text = ''.join(current_chunk)[-self.parent_chunk_overlap:] if self.parent_chunk_overlap > 0 else ''
                current_chunk = [overlap_text, sentence] if overlap_text else [sentence]
                current_length = len(overlap_text) + sentence_length
            else:
                current_chunk.append(sentence)
                current_length += sentence_length
        
        # 保存最后一个块
        if current_chunk:
            parent_chunks.append(''.join(current_chunk))
        
        return parent_chunks
    
    def create_sub_chunks(self, parent_text: str) -> List[str]:
        """
        在父块内创建子块。

        Args:
            parent_text: 父块文本

        Returns:
            子块文本列表
        """
        if len(parent_text) <= self.sub_chunk_size:
            return [parent_text]
        
        sub_chunks = []
        start = 0
        
        while start < len(parent_text):
            # 计算子块结束位置
            end = start + self.sub_chunk_size
            
            if end >= len(parent_text):
                # 最后一块，直接取到结尾
                sub_chunks.append(parent_text[start:])
                break
            
            # 尝试在句末标点处切分（向后查找）
            search_end = min(end + 50, len(parent_text))
            slice_text = parent_text[start:search_end]
            
            # 查找最佳切分点
            best_end = end
            for i in range(len(slice_text) - 1, self.sub_chunk_size - 50, -1):
                if slice_text[i] in '。！？.!?；;':
                    best_end = start + i + 1
                    break
            
            sub_chunks.append(parent_text[start:best_end])
            
            # 计算下一个起始位置（考虑重叠）
            start = best_end - self.sub_chunk_overlap if self.sub_chunk_overlap > 0 else best_end
            
            # 防止死循环
            if start >= len(parent_text):
                break
        
        return sub_chunks
    
    def _process_text(self, text: str, source_doc: str) -> List[HierarchicalChunk]:
        """
        处理文本并创建层次化切块。

        Args:
            text: 待处理的文本
            source_doc: 来源文档名称

        Returns:
            层次化切块列表
        """
        # 第一步：句子切分
        sentences = self.split_by_sentences(text)
        
        if not sentences:
            return []
        
        # 第二步：创建父块
        parent_texts = self.create_parent_chunks(sentences)
        
        # 第三步：为每个父块创建子块
        hierarchical_chunks = []
        for idx, parent_text in enumerate(parent_texts):
            sub_chunks = self.create_sub_chunks(parent_text)
            
            chunk = HierarchicalChunk(
                parent_text=parent_text,
                parent_index=idx,
                sub_chunks=sub_chunks,
                source_doc=source_doc,
                page_num=None,
                start_page=None,
                end_page=None
            )
            hierarchical_chunks.append(chunk)
        
        return hierarchical_chunks
    
    def _process_text_with_pages(self, paragraphs_with_pages: List[dict], source_doc: str) -> List[HierarchicalChunk]:
        """
        处理带页码信息的文本并创建层次化切块。

        Args:
            paragraphs_with_pages: 段落页码映射列表，格式为 [{'text': 段落文本, 'page': 页码}, ...]
            source_doc: 来源文档名称

        Returns:
            层次化切块列表
        """
        # 提取所有文本用于句子切分
        full_text = "\n\n".join([p['text'] for p in paragraphs_with_pages])
        
        # 第一步：句子切分
        sentences = self.split_by_sentences(full_text)
        
        if not sentences:
            return []
        
        # 创建句子到页码的映射
        # 为每个句子估算页码
        sentence_pages = []
        current_para_idx = 0
        current_para_offset = 0
        
        for sentence in sentences:
            # 找到这个句子属于哪个段落
            while current_para_idx < len(paragraphs_with_pages):
                para_text = paragraphs_with_pages[current_para_idx]['text']
                para_page = paragraphs_with_pages[current_para_idx]['page']
                
                # 在段落中查找句子
                sentence_pos = para_text.find(sentence, current_para_offset)
                if sentence_pos != -1:
                    sentence_pages.append(para_page)
                    current_para_offset = sentence_pos + len(sentence)
                    break
                else:
                    current_para_idx += 1
                    current_para_offset = 0
            else:
                # 如果没找到，使用最后一个段落的页码
                sentence_pages.append(paragraphs_with_pages[-1]['page'] if paragraphs_with_pages else 1)
        
        # 第二步：创建父块
        parent_texts = []
        parent_page_ranges = []  # 存储每个父块的起止页码
        
        current_chunk: List[str] = []
        current_length = 0
        current_start_page = sentence_pages[0] if sentence_pages else 1
        
        for i, sentence in enumerate(sentences):
            sentence_length = len(sentence)
            sentence_page = sentence_pages[i] if i < len(sentence_pages) else current_start_page
            
            # 如果当前句子本身就超过父块大小，直接作为一个块
            if sentence_length >= self.parent_chunk_size:
                # 先保存之前的累积内容
                if current_chunk:
                    parent_texts.append(''.join(current_chunk))
                    parent_page_ranges.append((current_start_page, sentence_page))
                    current_chunk = []
                    current_length = 0
                # 将长句子作为一个独立块
                parent_texts.append(sentence)
                parent_page_ranges.append((sentence_page, sentence_page))
                continue
            
            # 检查加入当前句子后是否会超过限制
            if current_length + sentence_length > self.parent_chunk_size and current_chunk:
                # 保存当前块
                parent_texts.append(''.join(current_chunk))
                parent_page_ranges.append((current_start_page, sentence_page))
                
                # 保留重叠部分
                overlap_text = ''.join(current_chunk)[-self.parent_chunk_overlap:] if self.parent_chunk_overlap > 0 else ''
                current_chunk = [overlap_text, sentence] if overlap_text else [sentence]
                current_length = len(overlap_text) + sentence_length
                current_start_page = sentence_page
            else:
                current_chunk.append(sentence)
                current_length += sentence_length
        
        # 保存最后一个块
        if current_chunk:
            last_page = sentence_pages[-1] if sentence_pages else current_start_page
            parent_texts.append(''.join(current_chunk))
            parent_page_ranges.append((current_start_page, last_page))
        
        # 第三步：为每个父块创建子块
        hierarchical_chunks = []
        for idx, (parent_text, (start_page, end_page)) in enumerate(zip(parent_texts, parent_page_ranges)):
            sub_chunks = self.create_sub_chunks(parent_text)
            
            chunk = HierarchicalChunk(
                parent_text=parent_text,
                parent_index=idx,
                sub_chunks=sub_chunks,
                source_doc=source_doc,
                page_num=start_page,  # 主页码使用起始页
                start_page=start_page,
                end_page=end_page
            )
            hierarchical_chunks.append(chunk)
        
        return hierarchical_chunks
    
    def parse(self, file_path: str, extract_page_info: bool = True) -> List[HierarchicalChunk]:
        """
        解析 DOCX 文件并创建层次化切块。

        Args:
            file_path: DOCX 文件路径
            extract_page_info: 是否提取页码信息，默认为 True

        Returns:
            层次化切块列表
        """
        if extract_page_info:
            text, paragraphs_with_pages = self.parse_docx_with_page_info(file_path)
            return self._process_text_with_pages(paragraphs_with_pages, file_path)
        else:
            text = self.parse_docx(file_path)
            return self._process_text(text, file_path)
    
    def parse_bytes(self, file_bytes: bytes, source_doc: str = "uploaded_doc.docx", extract_page_info: bool = True) -> List[HierarchicalChunk]:
        """
        从字节流解析文档。

        Args:
            file_bytes: DOCX 文件字节流
            source_doc: 文档名称
            extract_page_info: 是否提取页码信息，默认为 True

        Returns:
            层次化切块列表
        """
        if extract_page_info:
            # 对于字节流，我们使用估算方法
            text = self.parse_docx_bytes(file_bytes)
            return self._process_text_with_page_estimate(text, source_doc)
        else:
            text = self.parse_docx_bytes(file_bytes)
            return self._process_text(text, source_doc)
    
    def _process_text_with_page_estimate(self, text: str, source_doc: str, chars_per_page: int = 1800) -> List[HierarchicalChunk]:
        """
        基于字符数估算页码信息处理文本。

        默认假设标准 A4 页面大约每页 1800 字符。

        Args:
            text: 待处理的文本
            source_doc: 来源文档名称
            chars_per_page: 每页字符数估算值，默认 1800

        Returns:
            层次化切块列表
        """
        CHARS_PER_PAGE = max(1, chars_per_page)
        
        # 第一步：句子切分
        sentences = self.split_by_sentences(text)
        
        if not sentences:
            return []
        
        # 第二步：创建父块，同时记录页码范围
        parent_texts = []
        parent_page_ranges = []
        
        current_chunk: List[str] = []
        current_length = 0
        current_char_total = 0
        current_start_page = 1
        
        for sentence in sentences:
            sentence_length = len(sentence)
            
            # 计算这个句子开始时的页码
            sentence_start_page = (current_char_total // CHARS_PER_PAGE) + 1
            
            # 如果当前句子本身就超过父块大小，直接作为一个块
            if sentence_length >= self.parent_chunk_size:
                # 先保存之前的累积内容
                if current_chunk:
                    parent_texts.append(''.join(current_chunk))
                    parent_page_ranges.append((current_start_page, sentence_start_page))
                    current_chunk = []
                    current_length = 0
                
                # 将长句子作为一个独立块
                parent_texts.append(sentence)
                sentence_end_page = ((current_char_total + sentence_length) // CHARS_PER_PAGE) + 1
                parent_page_ranges.append((sentence_start_page, sentence_end_page))
                
                current_char_total += sentence_length
                current_start_page = sentence_end_page
                continue
            
            # 检查加入当前句子后是否会超过限制
            if current_length + sentence_length > self.parent_chunk_size and current_chunk:
                # 保存当前块
                parent_texts.append(''.join(current_chunk))
                parent_page_ranges.append((current_start_page, sentence_start_page))
                
                # 保留重叠部分
                overlap_text = ''.join(current_chunk)[-self.parent_chunk_overlap:] if self.parent_chunk_overlap > 0 else ''
                current_chunk = [overlap_text, sentence] if overlap_text else [sentence]
                current_length = len(overlap_text) + sentence_length
                current_start_page = sentence_start_page
            else:
                current_chunk.append(sentence)
                current_length += sentence_length
            
            current_char_total += sentence_length
        
        # 保存最后一个块
        if current_chunk:
            last_page = (current_char_total // CHARS_PER_PAGE) + 1
            parent_texts.append(''.join(current_chunk))
            parent_page_ranges.append((current_start_page, last_page))
        
        # 第三步：为每个父块创建子块
        hierarchical_chunks = []
        for idx, (parent_text, (start_page, end_page)) in enumerate(zip(parent_texts, parent_page_ranges)):
            sub_chunks = self.create_sub_chunks(parent_text)
            
            chunk = HierarchicalChunk(
                parent_text=parent_text,
                parent_index=idx,
                sub_chunks=sub_chunks,
                source_doc=source_doc,
                page_num=start_page,
                start_page=start_page,
                end_page=end_page
            )
            hierarchical_chunks.append(chunk)
        
        return hierarchical_chunks
    
    def get_all_sub_chunks(self, hierarchical_chunks: List[HierarchicalChunk]) -> List[dict]:
        """
        获取所有子块，包含元数据（包含页码信息）。

        Args:
            hierarchical_chunks: 层次化切块列表

        Returns:
            子块元数据列表
        """
        sub_chunks_with_meta = []
        
        for chunk in hierarchical_chunks:
            for sub_idx, sub_text in enumerate(chunk.sub_chunks):
                sub_chunks_with_meta.append({
                    'text': sub_text,
                    'parent_text': chunk.parent_text,
                    'parent_index': chunk.parent_index,
                    'sub_index': sub_idx,
                    'source_doc': chunk.source_doc,
                    'page_num': chunk.page_num,
                    'start_page': chunk.start_page,
                    'end_page': chunk.end_page
                })
        
        return sub_chunks_with_meta


class DocumentSegmentation(DocxParser):
    """
    文档分块与页码提取入口类。

    复用 DocxParser 的完整能力，提供统一的文档分块接口。
    """

    def segment_text(self, text: str, source_doc: str = "") -> List[HierarchicalChunk]:
        """
        对纯文本执行父子块切分，不做页码提取。

        Args:
            text: 待分块的文本
            source_doc: 来源文档名称

        Returns:
            层次化切块列表
        """
        return self._process_text(text, source_doc)

    def segment_text_to_sub_chunks(self, text: str, source_doc: str = "") -> List[dict]:
        """
        对纯文本直接输出可嵌入的子块列表。

        Args:
            text: 待分块的文本
            source_doc: 来源文档名称

        Returns:
            子块元数据列表
        """
        hierarchical_chunks = self.segment_text(text, source_doc)
        return self.get_all_sub_chunks(hierarchical_chunks)

    def segment_text_with_page_count(self, text: str, source_doc: str = "", total_pages: int = 0) -> List[HierarchicalChunk]:
        """
        根据总页数动态估算每页字符数，再执行带页码的父子块切分。

        Args:
            text: 待分块的文本
            source_doc: 来源文档名称
            total_pages: 文档总页数，用于估算每页字符数

        Returns:
            层次化切块列表
        """
        if not text:
            return []
        if total_pages > 0:
            chars_per_page = math.ceil(len(text) / total_pages)
            return self._process_text_with_page_estimate(text, source_doc, chars_per_page=chars_per_page)
        return self._process_text_with_page_estimate(text, source_doc)


# 便捷函数
def parse_docx(file_path: str,
               parent_chunk_size: int = 1200,
               sub_chunk_size: int = 300) -> List[HierarchicalChunk]:
    """
    便捷函数：使用 Docling 解析并分块 DOCX 文件。

    Args:
        file_path: DOCX 文件路径
        parent_chunk_size: 父块大小，默认 1200 字符
        sub_chunk_size: 子块大小，默认 300 字符

    Returns:
        层次化切块列表
    """
    parser = DocxParser(parent_chunk_size, sub_chunk_size)
    return parser.parse(file_path)


def parse_docx_bytes(file_bytes: bytes,
                     source_doc: str = "uploaded_doc.docx",
                     parent_chunk_size: int = 1200,
                     sub_chunk_size: int = 300) -> List[HierarchicalChunk]:
    """
    便捷函数：使用 Docling 从字节流解析并分块 DOCX。

    Args:
        file_bytes: DOCX 文件字节数据
        source_doc: 来源文档名称
        parent_chunk_size: 父块大小，默认 1200 字符
        sub_chunk_size: 子块大小，默认 300 字符

    Returns:
        层次化切块列表
    """
    parser = DocxParser(parent_chunk_size, sub_chunk_size)
    return parser.parse_bytes(file_bytes, source_doc)


