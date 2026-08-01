"""将 PDF 裁剪为从首页开始的固定页数，并在替换前校验结果。"""

import argparse
from pathlib import Path

import fitz


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    if args.pages < 1:
        raise ValueError("页数必须大于 0")
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"输出文件已存在: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}.tmp.pdf")
    if temporary.exists():
        temporary.unlink()

    source_document = fitz.open(source)
    try:
        if args.pages > source_document.page_count:
            raise ValueError(
                f"请求页数 {args.pages} 超过原文件页数 {source_document.page_count}"
            )
        cropped_document = fitz.open()
        try:
            cropped_document.insert_pdf(
                source_document,
                from_page=0,
                to_page=args.pages - 1,
            )
            cropped_document.save(temporary, garbage=4, deflate=True)
        finally:
            cropped_document.close()
    finally:
        source_document.close()

    checked_document = fitz.open(temporary)
    try:
        text_lengths = [
            len(checked_document[index].get_text())
            for index in range(checked_document.page_count)
        ]
        if checked_document.page_count != args.pages:
            raise RuntimeError("裁剪后的 PDF 页数校验失败")
        if any(length == 0 for length in text_lengths):
            raise RuntimeError("裁剪后的 PDF 存在无文本页面")
    finally:
        checked_document.close()

    temporary.replace(output)
    print(f"已生成 {output}，页数={args.pages}，最少字符数={min(text_lengths)}")


if __name__ == "__main__":
    main()
