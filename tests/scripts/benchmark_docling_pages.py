"""在内存保护线内测量 Docling 可稳定解析的 PDF 页数。"""

import argparse
import json
import os
import threading
import time
from pathlib import Path

import psutil
import torch
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

MEMORY_GUARD_EXIT_CODE = 86


def _build_converter(device: str) -> DocumentConverter:
    accelerator = AcceleratorOptions(
        device=AcceleratorDevice(device),
        num_threads=2,
    )
    pipeline_options = ThreadedPdfPipelineOptions(
        accelerator_options=accelerator,
        do_ocr=False,
        do_table_structure=True,
        ocr_batch_size=1,
        layout_batch_size=1,
        table_batch_size=1,
        queue_max_size=2,
        document_timeout=1800,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def _monitor_memory(
    stop_event: threading.Event,
    metrics: dict[str, int],
    min_available_bytes: int,
    max_rss_bytes: int,
) -> None:
    process = psutil.Process()
    while not stop_event.wait(0.2):
        rss = process.memory_info().rss
        available = psutil.virtual_memory().available
        metrics["peak_rss_bytes"] = max(metrics["peak_rss_bytes"], rss)
        metrics["min_available_bytes"] = min(metrics["min_available_bytes"], available)
        if available < min_available_bytes or rss > max_rss_bytes:
            os._exit(MEMORY_GUARD_EXIT_CODE)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--min-available-gb", type=float, default=1.0)
    parser.add_argument("--max-rss-gb", type=float, default=8.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA 不可用，不能执行指定的 Docling GPU 压测")
    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.set_per_process_memory_fraction(0.8)

    metrics = {
        "peak_rss_bytes": psutil.Process().memory_info().rss,
        "min_available_bytes": psutil.virtual_memory().available,
    }
    stop_event = threading.Event()
    monitor = threading.Thread(
        target=_monitor_memory,
        args=(
            stop_event,
            metrics,
            int(args.min_available_gb * 1024**3),
            int(args.max_rss_gb * 1024**3),
        ),
        daemon=True,
    )
    monitor.start()

    started_at = time.perf_counter()
    try:
        result = _build_converter(args.device).convert(
            args.pdf,
            raises_on_error=False,
            page_range=(1, args.pages),
        )
    finally:
        stop_event.set()
        monitor.join(timeout=1)

    page_count = len(result.pages)
    payload = {
        "requested_pages": args.pages,
        "parsed_pages": page_count,
        "status": str(result.status),
        "elapsed_seconds": round(time.perf_counter() - started_at, 2),
        "peak_rss_gb": round(metrics["peak_rss_bytes"] / 1024**3, 3),
        "min_available_gb": round(metrics["min_available_bytes"] / 1024**3, 3),
        "peak_cuda_allocated_gb": (
            round(torch.cuda.max_memory_allocated() / 1024**3, 3)
            if args.device == "cuda"
            else 0.0
        ),
        "text_chars": len(result.document.export_to_text()),
        "table_count": len(result.document.tables),
        "error_count": len(result.errors),
    }
    print(json.dumps(payload, ensure_ascii=False))
    if page_count != args.pages or "success" not in str(result.status).lower():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
