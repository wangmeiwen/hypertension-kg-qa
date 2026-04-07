"""
性能日志模块。

本模块用于记录系统性能信息，支持：
- 统一的耗时统计上下文管理器
- 结构化性能事件日志
- 基础敏感信息脱敏（手机号、邮箱、身份证号）

主要函数：
- log_duration: 记录代码块执行耗时
- log_perf_event: 记录结构化性能事件

"""

import logging
import time
import json
import re
import sys
from pathlib import Path
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

logger = logging.getLogger("perf")
_PHONE_PATTERN = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
_EMAIL_PATTERN = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_IDCARD_PATTERN = re.compile(r"(?<!\d)(\d{17}[\dXx]|\d{15})(?!\d)")


def _mask_pii_text(text: str) -> str:
    """
    对文本中的常见个人敏感信息进行脱敏。

    Args:
        text: 待脱敏的原始文本。

    Returns:
        str: 脱敏后的文本。
    """
    value = str(text or "")
    value = _PHONE_PATTERN.sub("<PHONE_MASKED>", value)
    value = _EMAIL_PATTERN.sub("<EMAIL_MASKED>", value)
    value = _IDCARD_PATTERN.sub("<ID_MASKED>", value)
    return value


def _mask_sensitive(value):
    """
    递归脱敏任意结构化对象中的敏感文本。

    Args:
        value: 待脱敏对象，支持 dict、list、str 或其他类型。

    Returns:
        Any: 脱敏后的对象。
    """
    if isinstance(value, dict):
        return {key: _mask_sensitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return _mask_pii_text(value)
    return value


def _ensure_logger_configured() -> None:
    """
    初始化性能日志记录器及输出目标。

    Args:
        None。

    Returns:
        None。
    """
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 文件输出：项目根目录/logs/perf.log
    project_root = Path(__file__).resolve().parents[2]
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "perf.log"

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


@contextmanager
def log_duration(operation: str, **kwargs):
    """
    记录代码块耗时毫秒（仅输出一次）。

    Args:
        operation: 操作名称，用于日志标识。
        **kwargs: 额外结构化字段，会以 key=value 形式输出。

    Returns:
        Generator[None, None, None]: 上下文管理器。
    """
    _ensure_logger_configured()

    start = time.perf_counter()
    kv = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    prefix = f"[性能] {operation}"

    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if kv:
            logger.info(f"{prefix} 耗时毫秒={elapsed_ms:.2f} {kv}")
        else:
            logger.info(f"{prefix} 耗时毫秒={elapsed_ms:.2f}")


def log_perf_event(event: str, **kwargs) -> None:
    """
    记录结构化性能事件并输出到性能日志。

    Args:
        event: 事件名称。
        **kwargs: 事件附加字段。

    Returns:
        None。
    """
    _ensure_logger_configured()
    payload = _mask_sensitive({"event": event, **kwargs})
    logger.info("[性能事件] %s", json.dumps(payload, ensure_ascii=False, default=str))


