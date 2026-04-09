import io
import logging
from pathlib import Path
from typing import ClassVar, Iterable, Literal, Type

from docling_core.types.doc import BoundingBox, CoordOrigin
from docling_core.types.doc.page import BoundingRectangle, TextCell
from pydantic import ConfigDict, Field

from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import OcrOptions
from docling.datamodel.settings import settings
from docling.models.base_ocr_model import BaseOcrModel
from docling.utils.profiling import TimeRecorder

from app.document_parser.glm_ocr_client import get_glm_ocr_client

logger = logging.getLogger(__name__)


class GlmVisionOcrOptions(OcrOptions):
    kind: ClassVar[Literal["glm_vision"]] = "glm_vision"
    lang: list[str] = ["zh", "en"]
    prompt: str = Field(
        default="请提取图片中的全部可读文字，按原始阅读顺序输出，仅返回纯文本。",
        description="发送给 GLM 视觉模型的 OCR 提示词。",
    )
    image_scale: float = Field(
        default=2.0,
        description="页面或位图裁剪渲染后送入 GLM OCR 的缩放倍数。",
        ge=1.0,
        le=4.0,
    )
    line_confidence: float = Field(
        default=0.9,
        description="填充到 Docling TextCell 的伪置信度。",
        ge=0.0,
        le=1.0,
    )

    model_config = ConfigDict(extra="forbid")


class GlmVisionOcrModel(BaseOcrModel):
    def __init__(
        self,
        *,
        enabled: bool,
        artifacts_path: Path | None,
        options: GlmVisionOcrOptions,
        accelerator_options: AcceleratorOptions,
    ):
        super().__init__(
            enabled=enabled,
            artifacts_path=artifacts_path,
            options=options,
            accelerator_options=accelerator_options,
        )
        self.options: GlmVisionOcrOptions
        self.client = get_glm_ocr_client() if enabled else None

    def _image_to_bytes(self, image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _split_lines(self, text: str) -> list[str]:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        if lines:
            return lines
        text = str(text or "").strip()
        return [text] if text else []

    def _build_cells_from_lines(
        self, lines: list[str], ocr_rect: BoundingBox
    ) -> list[TextCell]:
        if not lines:
            return []

        rect_height = max(1.0, float(ocr_rect.b - ocr_rect.t))
        step = rect_height / max(1, len(lines))
        cells: list[TextCell] = []
        for idx, line in enumerate(lines):
            top = ocr_rect.t + (idx * step)
            bottom = ocr_rect.t + ((idx + 1) * step)
            bbox = BoundingBox.from_tuple(
                coord=(ocr_rect.l, top, ocr_rect.r, bottom),
                origin=CoordOrigin.TOPLEFT,
            )
            cells.append(
                TextCell(
                    index=idx,
                    text=line,
                    orig=line,
                    confidence=self.options.line_confidence,
                    from_ocr=True,
                    rect=BoundingRectangle.from_bounding_box(bbox),
                )
            )
        return cells

    def __call__(
        self, conv_res: ConversionResult, page_batch: Iterable[Page]
    ) -> Iterable[Page]:
        if not self.enabled or self.client is None:
            yield from page_batch
            return

        for page in page_batch:
            assert page._backend is not None
            if not page._backend.is_valid():
                yield page
                continue

            with TimeRecorder(conv_res, "ocr"):
                ocr_rects = self.get_ocr_rects(page)
                all_ocr_cells: list[TextCell] = []

                for ocr_rect in ocr_rects:
                    if ocr_rect.area() == 0:
                        continue

                    high_res_image = page._backend.get_page_image(
                        scale=self.options.image_scale,
                        cropbox=ocr_rect,
                    )
                    result = self.client.recognize_image_bytes(
                        self._image_to_bytes(high_res_image)
                    )
                    text = str(result.get("text") or "").strip()
                    if not text:
                        continue

                    all_ocr_cells.extend(
                        self._build_cells_from_lines(
                            self._split_lines(text),
                            ocr_rect,
                        )
                    )

                    warnings = list(result.get("warnings") or [])
                    if warnings:
                        logger.debug(
                            "glm_docling_ocr_warnings page=%s warnings=%s",
                            page.page_no,
                            warnings,
                        )

                self.post_process_cells(all_ocr_cells, page)

            if settings.debug.visualize_ocr:
                self.draw_ocr_rects_and_cells(conv_res, page, ocr_rects)

            yield page

    @classmethod
    def get_options_type(cls) -> Type[OcrOptions]:
        return GlmVisionOcrOptions


def ocr_engines() -> dict[str, list[type[BaseOcrModel]]]:
    return {
        "ocr_engines": [
            GlmVisionOcrModel,
        ]
    }
