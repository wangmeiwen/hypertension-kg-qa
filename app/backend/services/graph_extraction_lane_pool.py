"""图谱抽取模型 lane 组件。"""

from __future__ import annotations

from typing import Any, Callable, Optional


class GraphExtractionLanePool:
    """管理 lane 状态、错误分类和单块图谱抽取。"""

    @staticmethod
    def classify_lane_error(error: BaseException | str | None) -> str:
        """将 lane 错误归类为 permanent 或 transient。"""
        error_text = str(error or "").strip().lower()
        if not error_text:
            return "transient"
        permanent_markers = (
            "401 unauthorized",
            "403 forbidden",
            "404 not found",
        )
        if any(marker in error_text for marker in permanent_markers):
            return "permanent"
        transient_markers = (
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "connection reset",
            "connection aborted",
            "connection error",
            "api connection error",
            "service unavailable",
            "server disconnected",
            "temporarily unavailable",
            "502",
            "503",
            "504",
            "network",
        )
        if any(marker in error_text for marker in transient_markers):
            return "transient"
        return "transient"

    @staticmethod
    def clone_lane_statuses(
        lane_statuses: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """复制 lane 状态用于写回任务响应。"""
        return [dict(item) for item in lane_statuses]

    @classmethod
    def count_active_lanes(cls, lane_statuses: list[dict[str, Any]]) -> int:
        """统计当前可立即提供服务的 lane 数量。"""
        return sum(
            1 for item in lane_statuses if item.get("status") in {"ready", "running"}
        )

    @classmethod
    def count_viable_lanes(cls, lane_statuses: list[dict[str, Any]]) -> int:
        """统计当前仍可恢复或继续提供服务的 lane 数量。"""
        return sum(
            1
            for item in lane_statuses
            if item.get("status") in {"ready", "running", "backoff"}
        )

    @staticmethod
    def format_lane_label(lane_config: dict[str, Any]) -> str:
        """格式化 lane 展示标签。"""
        lane_id = lane_config.get("lane_id")
        model_name = str(lane_config.get("model_name") or "")
        source = str(lane_config.get("source") or "")
        if source:
            return f"lane{lane_id}:{model_name} ({source})"
        return f"lane{lane_id}:{model_name}"

    @classmethod
    def build_lane_statuses(
        cls, lane_configs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """为可用模型 lane 创建初始运行状态。"""
        return [
            {
                "lane_id": int(item["lane_id"]),
                "label": cls.format_lane_label(item),
                "model_name": str(item["model_name"]),
                "status": "ready",
                "current_chunk_index": None,
                "current_attempt": 0,
                "last_error": "",
                "backoff_until": None,
            }
            for item in lane_configs
        ]

    @staticmethod
    def update_lane_status(
        lane_statuses: list[dict[str, Any]],
        lane_id: int,
        *,
        status: Optional[str] = None,
        current_chunk_index: Optional[int] = None,
        current_attempt: Optional[int] = None,
        last_error: Optional[str] = None,
        backoff_until: Optional[str] = None,
    ) -> None:
        """原地更新指定 lane 的运行状态。"""
        for item in lane_statuses:
            if int(item.get("lane_id") or 0) != lane_id:
                continue
            if status is not None:
                item["status"] = status
            item["current_chunk_index"] = current_chunk_index
            if current_attempt is not None:
                item["current_attempt"] = current_attempt
            if last_error is not None:
                item["last_error"] = last_error
            item["backoff_until"] = backoff_until
            return

    def extract_single_chunk_blocking(  # pragma: no cover - external LLM runtime
        self,
        chunk: str,
        chunk_index: int,
        total_chunks: int,
        total_chars: int,
        processed_chars: int,
        stage_callback: Callable[..., None],
        llm_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """在线程中执行单块图谱抽取（单次尝试）。

        Args:
            chunk: 当前文本块。
            chunk_index: 当前块序号。
            total_chunks: 总块数。
            total_chars: 文档总字符数。
            processed_chars: 已处理字符数。
            stage_callback: 阶段进度回调。
            llm_bundle: 当前 lane 使用的 LLM 配置和客户端上下文。

        Returns:
            dict[str, Any]: 当前块抽取出的节点查询、关系查询和统计数据。
        """
        from app.knowledge_graph.extract_graph_data_utils import (
            extract_text_to_graph_queries,
        )

        chunk_chars = len(chunk)
        stage_callback(
            "heartbeat",
            detail=f"文档解析完成，准备抽取第 {chunk_index}/{total_chunks} 段文本",
            progress_text=f"正在抽取第 {chunk_index}/{total_chunks} 段文本",
            metrics={
                "total_chars": total_chars,
                "processed_chars": processed_chars,
                "total_chunks": total_chunks,
                "completed_chunks": chunk_index - 1,
                "current_chunk_index": chunk_index,
                "current_chunk_chars": chunk_chars,
            },
        )
        graph_data = extract_text_to_graph_queries(
            chunk,
            chunk_chars,
            stage_callback=stage_callback,
            llm_bundle=llm_bundle,
        )
        return {
            "chunk_index": chunk_index,
            "chunk_chars": chunk_chars,
            "node_queries": graph_data["node_queries"],
            "relation_queries": graph_data["relation_queries"],
            "stats": graph_data["stats"],
        }

    @staticmethod
    def make_lane_stage_callback(  # pragma: no cover - external LLM lane callback
        base_callback: Callable[..., None],
        *,
        lane_id: int,
        chunk_index: int,
        total_chunks: int,
        attempt: int,
        max_attempts: int,
    ) -> Callable[..., None]:
        """为并发 lane 包装带上下文的阶段回调。

        Args:
            base_callback: 原始阶段回调。
            lane_id: 当前 lane 标识。
            chunk_index: 当前块序号。
            total_chunks: 总块数。
            attempt: 当前尝试次数。
            max_attempts: 最大尝试次数。

        Returns:
            Callable[..., None]: 会自动补充 lane、块和尝试次数前缀的回调。
        """

        prefix = f"[lane{lane_id} 第 {chunk_index}/{total_chunks} 块 第 {attempt}/{max_attempts} 次]"

        def _callback(
            action: str,
            stage: Optional[str] = None,
            detail: Optional[str] = None,
            progress_text: Optional[str] = None,
            metrics: Optional[dict[str, Any]] = None,
        ) -> None:
            next_detail = f"{prefix} {detail}" if detail else prefix
            next_progress = (
                f"{prefix} {progress_text}" if progress_text else next_detail
            )
            base_callback(
                action,
                stage=stage,
                detail=next_detail,
                progress_text=next_progress,
                metrics=metrics,
            )

        return _callback
