"""
文档解析测试脚本。

本脚本用于测试 DoclingParser 的文档解析功能，
包括 PDF 解析和图片 OCR 文字识别。

使用方法:
    python test_glm_ocr.py

依赖:
    - docling: IBM Docling 文档解析库
    - PyMuPDF (fitz): PDF 解析
"""

import os
import sys

from app.document_parser.docling_parser import DoclingParser, parse_document


def test_parse_pdf(pdf_path: str):
    """
    使用 DoclingParser 解析 PDF 文件。

    Args:
        pdf_path: PDF 文件路径
    """
    print("=" * 60)
    print(f"测试文件: {pdf_path}")
    print("=" * 60)

    if not os.path.exists(pdf_path):
        print(f"错误: 文件不存在 - {pdf_path}")
        return

    print("\n[1/2] 正在初始化解析器...")
    parser = DoclingParser(enable_image_ocr=True)
    print("      解析器初始化完成")

    print("\n[2/2] 正在解析文档...")
    try:
        result = parser.parse_file(pdf_path)
        print("      文档解析完成!")
    except Exception as e:
        print(f"错误: 文档解析失败 - {e}")
        import traceback
        traceback.print_exc()
        return

    print("\n" + "=" * 60)
    print("解析结果:")
    print("=" * 60)

    print(f"\n文档信息:")
    print(f"  - 来源: {result.source}")
    print(f"  - 格式: {result.format}")
    print(f"  - 页数: {len(result.pages)}")
    print(f"  - 表格数: {len(result.tables)}")
    print(f"  - 图片数: {len(result.images)}")

    if result.metadata:
        print(f"\n元数据:")
        for key, value in result.metadata.items():
            print(f"  - {key}: {value}")

    if result.images:
        print(f"\n图片信息:")
        for idx, img in enumerate(result.images):
            print(f"  图片 {idx + 1}:")
            if img.get("caption"):
                print(f"    - 标题: {img['caption']}")
            if img.get("ocr_text"):
                ocr_preview = img['ocr_text'][:100].replace('\n', ' ')
                print(f"    - OCR结果: {ocr_preview}...")
            if img.get("ocr_error"):
                print(f"    - OCR错误: {img['ocr_error']}")

    print("\n" + "=" * 60)
    print("文本内容预览 (前1000字符):")
    print("=" * 60)
    print(result.text[:1000] if result.text else "(无文本内容)")

    if len(result.text) > 1000:
        print("\n... (内容已截断)")

    print("\n" + "=" * 60)
    print("Markdown 预览 (前1000字符):")
    print("=" * 60)
    print(result.markdown[:1000] if result.markdown else "(无Markdown内容)")

    if len(result.markdown) > 1000:
        print("\n... (内容已截断)")


def test_parse_bytes(pdf_path: str):
    """
    使用字节流方式解析 PDF 文件。

    Args:
        pdf_path: PDF 文件路径
    """
    print("\n" + "=" * 60)
    print("测试字节流解析方式")
    print("=" * 60)

    with open(pdf_path, 'rb') as f:
        file_bytes = f.read()

    print(f"文件大小: {len(file_bytes) / 1024:.2f} KB")
    print("正在解析...")

    try:
        result = parse_document(file_bytes, filename=os.path.basename(pdf_path))
        print(f"解析完成! 共 {len(result.pages)} 页, {len(result.images)} 张图片")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 测试文件路径
    pdf_path = r"d:\graduate\HypertensionKG_QA_System\data\中国高血压防治指南2024_第10页.pdf"

    # 测试文件解析
    test_parse_pdf(pdf_path)

    # 测试字节流解析
    test_parse_bytes(pdf_path)
