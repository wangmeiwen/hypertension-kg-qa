"""
智谱 GLM-4V OCR 客户端模块。

本模块提供基于智谱 GLM-4.6V 视觉模型的 OCR 文字识别功能，支持：
- 图片字节流文字识别
- 统一返回结构封装

主要类：
- GlmOcrClient: GLM-4V OCR 客户端

"""

import base64
import io
import math
from typing import Any, Dict, Optional

from zhipuai import ZhipuAI

from app.common.config import Config


class GlmOcrClient:
    """
    智谱 GLM-4.6V OCR 客户端。

    封装智谱视觉模型调用，统一返回 OCR 文本结构。

    Attributes:
        model_name: 使用的模型名称
        client: ZhipuAI 客户端实例
    """

    def __init__(self, api_key: str, model_name: str = "glm-4.6v"):
        """
        初始化 OCR 客户端。

        Args:
            api_key: 智谱 AI API 密钥
            model_name: 模型名称，默认为 "glm-4.6v"
        """
        self.model_name = model_name
        self.client = ZhipuAI(api_key=api_key)

    def _recognize_single_pass(self, image_bytes: bytes, prompt: str) -> Dict[str, Any]:
        # 按官方 GLM-4.6V 示例执行单次视觉识别。
        """执行单次 OCR 识别请求。

        Args:
            image_bytes: 待识别图片字节流。
            prompt: 视觉模型提示词。

        Returns:
            Dict[str, Any]: 包含识别文本、原始响应和 finish_reason。
        """
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            thinking={"type": "enabled"},
        )

        text = ""
        finish_reason = ""
        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            text = str(getattr(message, "content", "") or "").strip()
            finish_reason = str(getattr(choices[0], "finish_reason", "") or "").strip().lower()

        raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)

        return {
            "text": text,
            "raw": raw,
            "finish_reason": finish_reason,
        }

    def _is_length_issue(self, exc: Exception) -> bool:
        # 判断异常是否属于长度或 token 限制。
        """判断异常是否由长度/Token 限制引起。

        Args:
            exc: 捕获到的异常对象。

        Returns:
            bool: 属于长度相关问题时返回 True。
        """
        err_text = str(exc).lower()
        markers = ["length", "max token", "max_tokens", "context length", "too long", "token", "413", "payload"]
        return any(marker in err_text for marker in markers)

    def _split_image_vertically(self, image_bytes: bytes, max_segment_height: int = 1500, overlap: int = 80) -> list[bytes]:
        # 将长图纵向切分为多个重叠子图，降低单次识别长度压力。
        """按高度将长图切分为可重叠子图。

        Args:
            image_bytes: 原始图片字节流。
            max_segment_height: 单段最大高度（像素）。
            overlap: 相邻子图重叠高度（像素）。

        Returns:
            list[bytes]: 分段后的 PNG 图片字节列表。
        """
        try:
            from PIL import Image
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
            return [image_bytes]

        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        if height <= max_segment_height:
            return [image_bytes]

        step = max(1, max_segment_height - overlap)
        segment_count = max(2, math.ceil((height - overlap) / step))
        segments: list[bytes] = []

        for idx in range(segment_count):
            y0 = idx * step
            y1 = min(height, y0 + max_segment_height)
            if y0 >= y1:
                continue
            cropped = img.crop((0, y0, width, y1))
            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            segments.append(buf.getvalue())
            if y1 >= height:
                break

        return segments if segments else [image_bytes]

    def _recognize_with_segments(self, image_bytes: bytes) -> Dict[str, Any]:
        # 使用分段识别流程处理长文本图片。
        """对长图执行分段 OCR 并合并结果。

        Args:
            image_bytes: 待识别图片字节流。

        Returns:
            Dict[str, Any]: 分段识别聚合结果（含策略、分段数与告警）。
        """
        segments = self._split_image_vertically(image_bytes)
        if len(segments) <= 1:
            single = self._recognize_single_pass(
                image_bytes,
                "请提取图片中的全部可读文字，按原始阅读顺序输出，仅返回纯文本。",
            )
            return {
                "text": single["text"],
                "raw": [single["raw"]],
                "strategy": "single",
                "segments_count": 1,
                "truncated": single["finish_reason"] == "length",
                "warnings": [],
            }

        texts = []
        raws = []
        warnings = []
        truncated = False

        for idx, segment_bytes in enumerate(segments):
            prompt = (
                f"这是长图的第 {idx + 1}/{len(segments)} 段，请只提取这一段的全部可读文字，"
                "按阅读顺序输出，仅返回纯文本，不要补充解释。"
            )
            try:
                part = self._recognize_single_pass(segment_bytes, prompt)
                part_text = str(part.get("text") or "").strip()
                if part_text:
                    texts.append(part_text)
                raws.append(part.get("raw", {}))
                if str(part.get("finish_reason") or "") == "length":
                    truncated = True
                    warnings.append(f"segment_{idx + 1}_truncated")
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
                warnings.append(f"segment_{idx + 1}_failed:{exc}")

        return {
            "text": "\n".join(texts).strip(),
            "raw": raws,
            "strategy": "segmented",
            "segments_count": len(segments),
            "truncated": truncated,
            "warnings": warnings,
        }

    def recognize_image_bytes(self, image_bytes: bytes) -> Dict[str, Any]:
        """
        识别图片中的文字。

        Args:
            image_bytes: 图片字节数据

        Returns:
            包含识别结果的字典，格式为
            {
                "text": str,
                "raw": dict | list,
                "strategy": str,
                "segments_count": int,
                "truncated": bool,
                "warnings": list[str],
            }
        """
        if not image_bytes:
            return {
                "text": "",
                "raw": {},
                "strategy": "single",
                "segments_count": 0,
                "truncated": False,
                "warnings": ["empty_image_bytes"],
            }

        prompt = "请提取图片中的全部可读文字，按原始阅读顺序输出，仅返回纯文本。"
        try:
            first_pass = self._recognize_single_pass(image_bytes, prompt)
            finish_reason = str(first_pass.get("finish_reason") or "")
            if finish_reason == "length":
                segmented = self._recognize_with_segments(image_bytes)
                segmented["warnings"] = ["single_pass_length"] + list(segmented.get("warnings") or [])
                return segmented
            return {
                "text": first_pass.get("text", ""),
                "raw": first_pass.get("raw", {}),
                "strategy": "single",
                "segments_count": 1,
                "truncated": False,
                "warnings": [],
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            if self._is_length_issue(exc):
                segmented = self._recognize_with_segments(image_bytes)
                segmented["warnings"] = [f"single_pass_exception:{exc}"] + list(segmented.get("warnings") or [])
                return segmented
            raise


_glm_ocr_client: Optional[GlmOcrClient] = None


def get_glm_ocr_client() -> GlmOcrClient:
    """
    获取全局单例 OCR 客户端。

    Returns:
        GlmOcrClient 单例实例
    """
    global _glm_ocr_client
    if _glm_ocr_client is None:
        conf = Config()
        _glm_ocr_client = GlmOcrClient(api_key=conf.ZHIPU_API_KEY)
    return _glm_ocr_client

