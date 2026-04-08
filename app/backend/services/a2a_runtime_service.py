"""A2A 运行时服务。"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.services.session_memory_service import SessionMemoryService
from app.common.feature_flags import get_runtime_flag_snapshot

if TYPE_CHECKING:
    from app.a2a import A2AService
    from app.a2a.agent import GraphSubAgent, RagSubAgent


_CYPHER_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class A2ARuntimeService:
    """封装 A2A/MCP 单例与子代理执行逻辑。"""

    def __init__(
        self,
        runtime_container: BackendRuntimeContainer,
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            runtime_container: 函数输入参数。
            log_api_error: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._runtime = runtime_container
        self._log_api_error = log_api_error

    def get_runtime_flag_snapshot(self) -> dict[str, Any]:
        """获取运行时开关快照。"""
        return get_runtime_flag_snapshot()

    @staticmethod
    def validate_cypher_identifier(raw_name: Any) -> Optional[str]:
        """校验 Cypher 标识符是否合法。"""
        name = str(raw_name).strip()
        if not name:
            return None
        if not _CYPHER_IDENTIFIER_PATTERN.match(name):
            return None
        return name

    @classmethod
    def sanitize_graph_properties(cls, raw_properties: Any) -> Dict[str, Any]:
        """清洗图谱属性键。"""
        if raw_properties is None:
            return {}
        if not isinstance(raw_properties, dict):
            raise ValueError("属性必须是对象")

        sanitized: Dict[str, Any] = {}
        for key, value in raw_properties.items():
            safe_key = cls.validate_cypher_identifier(key)
            if not safe_key:
                raise ValueError(f"非法属性键: {key}")
            sanitized[safe_key] = value
        return sanitized

    def get_mcp(self) -> Any:
        """获取或初始化 MCP 服务。"""
        if self._runtime.mcp_server is None:
            from app.mcp.tools import get_mcp_server

            self._runtime.mcp_server = get_mcp_server()
        return self._runtime.mcp_server

    def get_session_memory_service(self) -> SessionMemoryService:
        """获取或初始化会话记忆服务。"""
        if self._runtime.session_memory_service is None:
            self._runtime.session_memory_service = SessionMemoryService()
        return self._runtime.session_memory_service

    def get_graph_subagent(self) -> Any:
        """获取图谱子代理单例。"""
        if self._runtime.graph_subagent is None:
            from app.a2a.agent.graph_subagent import GraphSubAgent

            self._runtime.graph_subagent = GraphSubAgent(self.get_mcp())
        return self._runtime.graph_subagent

    def get_rag_subagent(self) -> Any:
        """获取检索子代理单例。"""
        if self._runtime.rag_subagent is None:
            from app.a2a.agent.rag_subagent import RagSubAgent

            self._runtime.rag_subagent = RagSubAgent(self.get_mcp())
        return self._runtime.rag_subagent

    async def _run_a2a_query(
        self,
        input_text: str,
        context_id: str = "",
        user_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """主编排代理执行入口。"""
        meta = metadata if isinstance(metadata, dict) else {}
        session_id = str(context_id or meta.get("session_id") or "").strip()
        safe_user_id = str(user_id or meta.get("user_id") or "").strip()
        strategy = str(meta.get("strategy") or "iterative").strip().lower() or "iterative"
        raw_max_turns = meta.get("max_turns")
        max_turns: Optional[int] = raw_max_turns if isinstance(raw_max_turns, int) and raw_max_turns > 0 else None
        original_input_text = str(meta.get("original_input_text") or input_text or "").strip()
        memory_context_obj = meta.get("memory_context")
        memory_context = memory_context_obj if isinstance(memory_context_obj, dict) else {}

        from app.a2a.agent import process_query

        return await process_query(
            input_text=str(input_text or ""),
            session_id=session_id,
            user_id=safe_user_id,
            strategy=strategy,
            max_turns=max_turns,
            original_input_text=original_input_text,
            memory_context=memory_context,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
        )

    @staticmethod
    def _build_subagent_requests(
        input_text: str,
        metadata: Dict[str, Any],
        default_tool_name: str,
    ) -> List[Dict[str, Any]]:
        """从 metadata 组装子代理请求列表。"""
        requests_obj = metadata.get("requests")
        if isinstance(requests_obj, list):
            rows: List[Dict[str, Any]] = []
            for idx, item in enumerate(requests_obj, start=1):
                if not isinstance(item, dict):
                    continue
                args_obj = item.get("arguments")
                arguments = args_obj if isinstance(args_obj, dict) else {}
                subquery = str(item.get("subquery") or input_text or "").strip()
                tool_name = str(item.get("tool_name") or default_tool_name).strip() or default_tool_name
                rows.append(
                    {
                        "subquery_id": int(item.get("subquery_id") or idx),
                        "subquery": subquery,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "reason": str(item.get("reason") or "").strip(),
                    }
                )
            if rows:
                return rows

        args_obj = metadata.get("arguments")
        arguments = args_obj if isinstance(args_obj, dict) else {}
        subquery = str(metadata.get("subquery") or input_text or "").strip()
        tool_name = str(metadata.get("tool_name") or default_tool_name).strip() or default_tool_name
        return [
            {
                "subquery_id": 1,
                "subquery": subquery,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": str(metadata.get("reason") or "").strip(),
            }
        ]

    async def _run_graph_a2a_query(
        self,
        input_text: str,
        context_id: str = "",
        user_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """图谱子代理执行入口。"""
        _ = cancel_check
        meta = metadata if isinstance(metadata, dict) else {}
        session_id = str(context_id or meta.get("session_id") or "").strip()
        safe_user_id = str(user_id or meta.get("user_id") or "").strip()
        reason = str(meta.get("reason") or "a2a_graph_rpc").strip()
        requests = self._build_subagent_requests(
            input_text=str(input_text or ""),
            metadata=meta,
            default_tool_name="kg_entity_relation_search",
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "graph_batch_start",
                    "detail": f"graph requests={len(requests)}",
                    "tool_name": "graph_subagent",
                }
            )

        result, result_meta = await self.get_graph_subagent().invoke_batch(
            requests=requests,
            user_query=str(input_text or ""),
            session_id=session_id,
            user_id=safe_user_id,
            reason=reason,
        )
        payload_obj = result.get("result") if isinstance(result, dict) else {}
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        summary_obj = payload.get("summary")
        summary = summary_obj if isinstance(summary_obj, dict) else {}
        items_obj = payload.get("items")
        items = items_obj if isinstance(items_obj, list) else []
        signal_count = int(summary.get("signal_count") or 0)
        total_requests = int(summary.get("total_requests") or len(requests))
        note = str(summary.get("note") or "").strip()
        if not note:
            note = f"Graph 子代理完成 {total_requests} 个检索任务"
        confidence = 0.75 if signal_count > 0 else 0.45
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "graph_batch_done",
                    "detail": note,
                    "tool_name": "graph_subagent",
                }
            )
        return {
            "output": note,
            "output_type": "graph_subagent_batch",
            "confidence": confidence,
            "tool_observations": items,
            "graph_result": result,
            "graph_meta": result_meta,
            "telemetry": {
                "tool_calls": total_requests,
                "graph_batch_size": total_requests,
                "graph_signal_count": signal_count,
                "graph_ok_count": int(summary.get("ok_count") or 0),
            },
            "evidence_summary": {
                "evidence_count": signal_count,
                "conflict_count": 0,
                "hop_coverage": 1.0 if signal_count > 0 else 0.0,
                "uncovered_points": [],
                "high_risk": False,
                "high_risk_tokens": [],
                "disambiguation_ambiguous_count": 0,
                "community_count": 0,
                "top_community_score": 0.0,
                "force_supplement_required": False,
            },
        }

    async def _run_retrieval_a2a_query(
        self,
        input_text: str,
        context_id: str = "",
        user_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """检索子代理执行入口。"""
        _ = cancel_check
        meta = metadata if isinstance(metadata, dict) else {}
        session_id = str(context_id or meta.get("session_id") or "").strip()
        safe_user_id = str(user_id or meta.get("user_id") or "").strip()
        reason = str(meta.get("reason") or "a2a_retrieval_rpc").strip()
        requests = self._build_subagent_requests(
            input_text=str(input_text or ""),
            metadata=meta,
            default_tool_name="rag_search",
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "retrieval_batch_start",
                    "detail": f"retrieval requests={len(requests)}",
                    "tool_name": "rag_subagent",
                }
            )

        result, result_meta = await self.get_rag_subagent().invoke_batch(
            requests=requests,
            user_query=str(input_text or ""),
            session_id=session_id,
            user_id=safe_user_id,
            reason=reason,
        )
        payload_obj = result.get("result") if isinstance(result, dict) else {}
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        summary_obj = payload.get("summary")
        summary = summary_obj if isinstance(summary_obj, dict) else {}
        items_obj = payload.get("items")
        items = items_obj if isinstance(items_obj, list) else []
        signal_count = int(summary.get("signal_count") or 0)
        total_requests = int(summary.get("total_requests") or len(requests))
        note = str(summary.get("note") or "").strip()
        if not note:
            note = f"Retrieval 子代理完成 {total_requests} 个检索任务"
        confidence = 0.72 if signal_count > 0 else 0.42
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "retrieval_batch_done",
                    "detail": note,
                    "tool_name": "rag_subagent",
                }
            )
        return {
            "output": note,
            "output_type": "retrieval_subagent_batch",
            "confidence": confidence,
            "tool_observations": items,
            "retrieval_result": result,
            "retrieval_meta": result_meta,
            "telemetry": {
                "tool_calls": total_requests,
                "retrieval_batch_size": total_requests,
                "retrieval_signal_count": signal_count,
                "retrieval_ok_count": int(summary.get("ok_count") or 0),
            },
            "evidence_summary": {
                "evidence_count": signal_count,
                "conflict_count": 0,
                "hop_coverage": 1.0 if signal_count > 0 else 0.0,
                "uncovered_points": [],
                "high_risk": False,
                "high_risk_tokens": [],
                "disambiguation_ambiguous_count": 0,
                "community_count": 0,
                "top_community_score": 0.0,
                "force_supplement_required": False,
            },
        }

    def get_a2a(self) -> Any:
        """获取主编排 A2A 服务。"""
        if self._runtime.a2a_service is None:
            from app.a2a import A2AService

            self._runtime.a2a_service = A2AService(
                run_query=self._run_a2a_query,
                agent_name="Hypertension Orchestrator Agent",
                agent_description="高血压问答多 Agent 编排代理（A2A 适配）",
                protocol_version="0.2.5",
                agent_url="/a2a/rpc",
                events_base_path="/a2a/tasks",
            )
        return self._runtime.a2a_service

    def get_a2a_graph(self) -> Any:
        """获取图谱子代理 A2A 服务。"""
        if self._runtime.a2a_graph_service is None:
            from app.a2a import A2AService

            self._runtime.a2a_graph_service = A2AService(
                run_query=self._run_graph_a2a_query,
                agent_name="Hypertension Graph Agent",
                agent_description="图谱检索与关系证据代理（A2A 适配）",
                protocol_version="0.2.5",
                agent_url="/a2a/graph/rpc",
                events_base_path="/a2a/graph/tasks",
            )
        return self._runtime.a2a_graph_service

    def get_a2a_retrieval(self) -> Any:
        """获取检索子代理 A2A 服务。"""
        if self._runtime.a2a_retrieval_service is None:
            from app.a2a import A2AService

            self._runtime.a2a_retrieval_service = A2AService(
                run_query=self._run_retrieval_a2a_query,
                agent_name="Hypertension Retrieval Agent",
                agent_description="语义检索与文本证据代理（A2A 适配）",
                protocol_version="0.2.5",
                agent_url="/a2a/retrieval/rpc",
                events_base_path="/a2a/retrieval/tasks",
            )
        return self._runtime.a2a_retrieval_service

    async def build_a2a_runtime_snapshot(self, limit_per_service: int = 200) -> Dict[str, Any]:
        """汇总 A2A 运行时任务快照。"""
        services = {
            "orchestrator": self.get_a2a(),
            "graph": self.get_a2a_graph(),
            "retrieval": self.get_a2a_retrieval(),
        }
        result: Dict[str, Any] = {}
        total = 0
        for name, service in services.items():
            rows = await service.alist_tasks(limit=limit_per_service)
            counter: Dict[str, int] = {}
            for row in rows:
                status_obj = row.get("status")
                status = status_obj if isinstance(status_obj, dict) else {}
                state = str(status.get("state") or "unknown")
                counter[state] = int(counter.get(state) or 0) + 1
            result[name] = {
                "recent_tasks": len(rows),
                "states": counter,
            }
            total += len(rows)
        return {
            "recent_total_tasks": total,
            "services": result,
        }

    async def measure_event_loop_lag(self, samples: int = 20, interval_ms: int = 20) -> Dict[str, Any]:
        """采样估算当前事件循环延迟。"""
        safe_samples = max(5, min(int(samples), 200))
        safe_interval_ms = max(1, min(int(interval_ms), 200))
        interval_s = float(safe_interval_ms) / 1000.0
        lags_ms: list[float] = []

        for _ in range(safe_samples):
            started = time.perf_counter()
            await asyncio.sleep(interval_s)
            elapsed = time.perf_counter() - started
            lag_ms = max(0.0, (elapsed - interval_s) * 1000.0)
            lags_ms.append(lag_ms)

        sorted_lags = sorted(lags_ms)

        def pct(p: float) -> float:
            """执行pct流程并返回结果。
            
            Args:
                p: 计算参数。
            
            Returns:
                float: 数值结果。
            """
            if not sorted_lags:
                return 0.0
            if len(sorted_lags) == 1:
                return sorted_lags[0]
            rank = max(0.0, min(1.0, p)) * (len(sorted_lags) - 1)
            low = int(rank)
            high = min(low + 1, len(sorted_lags) - 1)
            weight = rank - low
            return sorted_lags[low] * (1.0 - weight) + sorted_lags[high] * weight

        return {
            "samples": safe_samples,
            "interval_ms": safe_interval_ms,
            "avg_ms": round(sum(sorted_lags) / max(1, len(sorted_lags)), 2),
            "p50_ms": round(pct(0.50), 2),
            "p95_ms": round(pct(0.95), 2),
            "p99_ms": round(pct(0.99), 2),
            "max_ms": round(sorted_lags[-1] if sorted_lags else 0.0, 2),
        }

    async def shutdown_all(self) -> None:
        """关闭所有 A2A 服务单例。"""
        if self._runtime.a2a_service is not None:
            await self._runtime.a2a_service.ashutdown()
            self._runtime.a2a_service = None
        if self._runtime.a2a_graph_service is not None:
            await self._runtime.a2a_graph_service.ashutdown()
            self._runtime.a2a_graph_service = None
        if self._runtime.a2a_retrieval_service is not None:
            await self._runtime.a2a_retrieval_service.ashutdown()
            self._runtime.a2a_retrieval_service = None
