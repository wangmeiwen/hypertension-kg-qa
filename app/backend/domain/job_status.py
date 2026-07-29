"""后台任务状态契约。"""

from enum import StrEnum


class BackgroundJobStatus(StrEnum):
    """图谱与文档后台任务共享的状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
