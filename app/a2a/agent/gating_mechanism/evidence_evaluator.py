"""证据评估与终答门控模块。

本模块对检索轨迹进行结构化评估，支持证据计数、冲突检测、覆盖率计算与终答门控。

主要能力：
- 证据抽取与质量评估：从工具调用轨迹中提取结构化证据
- 多跳覆盖与子问题绑定：验证复杂问题的子查询是否都有证据支持
- 冲突检测：识别不同来源证据间的阈值矛盾
- 终答门控与置信度限制：综合多维度信号判断是否允许生成最终答案

使用场景：
    在 LangGraph 工作流的终答节点前调用，评估当前检索结果是否充分，
    决定是否直接生成答案或触发回溯进行补充检索。
"""

import logging
import os
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    """读取并归一化浮点环境变量。"""
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = float(raw)
    except ValueError:
        value = float(default)
    if value < min_value:
        return float(min_value)
    if value > max_value:
        return float(max_value)
    return float(value)

# 置信度上限配置
# 根据证据数量限制最终置信度上限，防止证据不足时过度自信
_EVIDENCE_CONFIDENCE_CAP: Dict[int, float] = {
    0: _env_float("EVIDENCE_CONFIDENCE_CAP_0", 0.30),  # 无证据时最高置信度
    1: _env_float("EVIDENCE_CONFIDENCE_CAP_1", 0.50),  # 1条证据时最高置信度
    2: _env_float("EVIDENCE_CONFIDENCE_CAP_2", 0.70),  # 2条证据时最高置信度
}
_DEFAULT_CONFIDENCE_CAP: float = _env_float("DEFAULT_CONFIDENCE_CAP", 0.90)  # 3条及以上证据时默认上限

# 冲突惩罚配置
# 检测到证据冲突时的置信度扣减规则
_CONFLICT_PENALTY: float = _env_float("CONFLICT_PENALTY", 0.15)  # 每条冲突扣减置信度
_MIN_CONFIDENCE: float = _env_float("MIN_CONFIDENCE", 0.30)    # 置信度最低下限

# 场景降级上限配置
# 特定场景下对置信度的额外限制
_DEGRADED_CONFIDENCE_CAP: float = _env_float("DEGRADED_CONFIDENCE_CAP", 0.40)              # 降级回答时上限
_INSUFFICIENT_SUBQUERY_CONFIDENCE_CAP: float = _env_float("INSUFFICIENT_SUBQUERY_CONFIDENCE_CAP", 0.40)  # 子问题证据不足时上限
_UNCOVERED_SUBQUERY_CONFIDENCE_CAP: float = _env_float("UNCOVERED_SUBQUERY_CONFIDENCE_CAP", 0.50)     # 子问题未覆盖时上限
_HIGH_RISK_CONFIDENCE_CAP: float = _env_float("HIGH_RISK_CONFIDENCE_CAP", 0.50)              # 高风险场景下上限
_AMBIGUITY_CONFIDENCE_CAP: float = _env_float("AMBIGUITY_CONFIDENCE_CAP", 0.55)              # 存在实体歧义时上限
_NO_COMMUNITY_CONFIDENCE_CAP: float = _env_float("NO_COMMUNITY_CONFIDENCE_CAP", 0.60)           # 复杂问题无社区时上限

# 高风险关键词集合
# 用于检测用户问题或检索结果中是否涉及高风险医疗场景
# 匹配到这些关键词时会降低置信度上限并触发安全警告
_HIGH_RISK_KEYWORDS: set[str] = {
    "高血压急症",
    "高血压危象",
    "妊娠高血压",
    "子痫",
    "胸痛",
    "脑卒中",
    "意识障碍",
    "器官衰竭",
    "肾衰",
    "急诊",
    "昏迷",
}

# 语义检索来源集合
# 这些来源的检索结果会参与语义质量评估
# 只有来自这些来源的证据才会计算语义相似度分数
_SEMANTIC_SOURCES: set[str] = {"kg_entity_relation_search", "rag_search"}

# 覆盖轴关键词
_COMPARATOR_COVERAGE_HINTS: tuple[str, ...] = (
    "comparison",
    "comparator",
    "compare",
    "versus",
    "vs",
    "head_to_head",
    "比较",
    "对比",
    "相比",
    "差异",
    "单药",
    "复方",
)
_OUTCOMES_COVERAGE_HINTS: tuple[str, ...] = (
    "outcome",
    "outcomes",
    "efficacy",
    "safety",
    "risk",
    "benefit",
    "疗效",
    "效果",
    "结局",
    "安全",
    "风险",
    "获益",
    "降压",
    "靶器官",
    "保护",
)

# 血压阈值正则表达式
# 用于从证据文本中提取血压数值，检测不同来源的阈值冲突
# 匹配格式：
#   - "140/90 mmHg"（标准血压表示）
#   - ">=140mmHg"（带比较运算符）
#   - "≤ 120/80 mmHg"（带空格和Unicode符号）
_THRESHOLD_PHRASE_PATTERN: re.Pattern = re.compile(
    r"([<>≤≥]?\s*\d{2,3}\s*/\s*\d{2,3}\s*mmHg|[<>≤≥]?\s*\d{2,3}\s*mmHg)",
    re.IGNORECASE
)

# 证据动作集合
# 这些动作的执行结果会被视为有效证据
_EVIDENCE_ACTIONS: set[str] = {"call_tool", "subquery_parallel_call", "subquery_dependent_call"}


def _semantic_min_score() -> float:
    """获取语义检索证据的最小有效分数阈值。

    从环境变量 SEMANTIC_EVIDENCE_MIN_SCORE 读取配置，用于判断
    语义检索结果（如实体相似度、RAG 分数）是否达到可信标准。

    低于此阈值的证据将被视为低质量，可能触发回溯或降低置信度。

    Returns:
        float: 语义证据最小有效分数，默认 0.65，范围 [0.0, 1.0]。

    """
    raw = str(os.getenv("SEMANTIC_EVIDENCE_MIN_SCORE", "0.65")).strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.65
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value

def _semantic_min_score_for_source(source: str) -> float:
    """按数据源获取语义证据阈值。

    优先读取来源级环境变量，其次使用来源默认阈值，最后回退到全局阈值。

    环境变量格式：SEMANTIC_EVIDENCE_MIN_SCORE_<SOURCE>
    例如：SEMANTIC_EVIDENCE_MIN_SCORE_KG_ENTITY_RELATION_SEARCH

    Args:
        source: 证据来源工具名。

    Returns:
        float: 归一化到 [0.0, 1.0] 的阈值。
    """
    normalized_source = str(source or "").strip().lower()
    default_threshold = _semantic_min_score()

    source_defaults: Dict[str, float] = {
        "kg_entity_relation_search": 0.50,
        "rag_search": default_threshold,
    }

    source_key = re.sub(r"[^a-z0-9]+", "_", normalized_source.upper()).strip("_")

    env_name = (
        f"SEMANTIC_EVIDENCE_MIN_SCORE_{source_key}"
        if source_key
        else ""
    )

    if env_name:
        raw = str(os.getenv(env_name, "")).strip()

        if raw:
            try:
                value = float(raw)
            except ValueError:
                value = source_defaults.get(normalized_source, default_threshold)
        else:
            value = source_defaults.get(normalized_source, default_threshold)
    else:
        value = source_defaults.get(normalized_source, default_threshold)

    if value < 0:
        return 0.0

    if value > 1:
        return 1.0

    return value


def _safe_float(raw: Any, default: float = 0.0) -> float:
    """安全地将输入转换为浮点数。

    处理各种可能的输入类型（字符串、数字、None等），
    转换失败时返回默认值，避免类型错误导致程序崩溃。

    Args:
        raw: 待转换的原始值，可以是字符串、数字或其他类型。
        default: 转换失败时的默认值，默认为 0.0。

    Returns:
        float: 转换后的浮点数，失败时返回 `default`。
    """
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(f"类型转换失败: 无法将 {raw!r} 转换为浮点数，使用默认值 {default}")
        return default


def count_evidence_from_traces(traces: List[Dict[str, Any]]) -> int:
    """从工具执行轨迹中统计有效证据条数。

    遍历工具调用轨迹，统计 records、hits、entities、relationships
    等各类返回数据的总条数，作为证据数量的粗略估计。

    只统计以下动作的结果：call_tool、subquery_parallel_call、subquery_dependent_call
    只统计 result.ok 为 True 的成功调用。

    Args:
        traces: 工具执行轨迹列表，每个轨迹包含 action、result、tool_name 等信息。

    Returns:
        int: 有效证据的总条数。

    """
    count = 0
    for trace in traces:
        if str(trace.get("action") or "") not in _EVIDENCE_ACTIONS:
            continue
        result = trace.get("result", {})
        if not isinstance(result, dict):
            continue
        if not result.get("ok"):
            continue
        payload = result.get("result", {})
        if not isinstance(payload, dict):
            continue
        for key in ("records", "hits", "entities", "relationships"):
            items = payload.get(key)
            if isinstance(items, list):
                count += len(items)
    return count


def extract_evidence_items(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从工具轨迹中提取结构化的证据条目。

    解析工具返回结果，提取实体、关系、文本片段等证据信息，
    并基于实体名称或文本内容前80字符进行去重。

    提取的数据源包括：
    - Neo4j 节点记录（records）：提取 name 和 labels
    - 实体搜索结果（entities）：提取 name/text 和 score
    - 关系数据（relationships）：提取 source_name、target_name、relation_type
    - RAG 文本片段（hits）：提取 text 和 score

    Args:
        traces: 工具执行轨迹列表，每个轨迹包含 action、result、tool_name、turn 等。

    Returns:
        List[Dict[str, Any]]: 结构化证据条目列表，每个条目包含：
            - source (str): 证据来源工具名称
            - content (str): 证据内容摘要（实体名、关系描述或文本片段前200字符）
            - entities (List[str]): 关联的实体名称列表
            - confidence (float): 归一化后的置信度分数 [0.0, 1.0]
            - raw_score (Optional[float]): 原始相似度分数，可能为 None
            - turn (int): 所属轮次，用于追踪多跳推理
    """
    evidence_items: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    for trace in traces:
        if str(trace.get("action") or "") not in _EVIDENCE_ACTIONS:
            continue
        result = trace.get("result", {})
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        payload = result.get("result", {})
        if not isinstance(payload, dict):
            continue
        source = trace.get("tool_name", "unknown")
        turn = trace.get("turn", 0)
        # 提取 Neo4j 节点记录
        for record in payload.get("records") or []:
            if not isinstance(record, dict):
                continue
            props = record.get("properties", {})
            name = str(props.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            labels = record.get("labels", [])
            evidence_items.append({
                "source": source,
                "content": f"{','.join(labels)}: {name}",
                "entities": [name],
                "confidence": 0.8,
                "raw_score": None,
                "turn": turn,
            })
        # 提取实体搜索结果
        for entity in payload.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or entity.get("text") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            score = _safe_float(entity.get("score"), 0.0)
            evidence_items.append({
                "source": source,
                "content": f"{entity.get('label', 'Entity')}: {name}",
                "entities": [name],
                "confidence": min(score, 1.0) if score > 0 else 0.7,
                "raw_score": score if score > 0 else None,
                "turn": turn,
            })
        # 提取关系数据
        for rel in payload.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            src = str(rel.get("source_name") or "").strip()
            dst = str(rel.get("target_name") or "").strip()
            rel_type = str(rel.get("relation_type") or "").strip()
            rel_key = f"{src}-{rel_type}-{dst}"
            if not src or not dst or rel_key in seen_names:
                continue
            seen_names.add(rel_key)
            evidence_items.append({
                "source": source,
                "content": f"{src} -[{rel_type}]-> {dst}",
                "entities": [src, dst],
                "confidence": min(_safe_float(rel.get("score"), 0.7), 1.0),
                "raw_score": _safe_float(rel.get("score"), 0.0) or None,
                "turn": turn,
            })
        # 提取 RAG 文本片段
        for hit in payload.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            text = str(hit.get("text") or "").strip()
            if not text:
                continue
            hit_key = text[:80]
            if hit_key in seen_names:
                continue
            seen_names.add(hit_key)
            evidence_items.append({
                "source": source,
                "content": text[:200],
                "entities": [],
                "confidence": min(_safe_float(hit.get("score"), 0.6), 1.0),
                "raw_score": _safe_float(hit.get("score"), 0.0) or None,
                "turn": turn,
            })
    return evidence_items


def detect_conflicts(evidence_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """检测证据间的阈值冲突。

    针对同一实体，检查不同来源的证据中是否存在不一致的血压阈值。
    仅当提取到可比较的数值短语且数值不一致时，才判定为冲突。

    注意：不再将"同实体多来源出现"直接判为冲突，避免误报。
    只有当不同来源提供了不同的血压阈值时才标记为冲突。

    Args:
        evidence_items: 证据条目列表，每个条目包含 source、content、entities 等。

    Returns:
        List[Dict[str, Any]]: 冲突列表，每个冲突包含：
            - entity (str): 冲突涉及的实体名称
            - sources (List[str]): 冲突来源列表（排序后）
            - evidence_count (int): 涉及证据数量
            - conflict_type (str): 冲突类型，固定为 "threshold_mismatch"
            - thresholds_by_source (Dict[str, List[str]]): 各来源的阈值短语列表
    """
    conflicts: List[Dict[str, Any]] = []
    entity_sources: Dict[str, List[Dict[str, Any]]] = {}
    for item in evidence_items:
        for entity in item.get("entities", []):
            entity_sources.setdefault(entity, []).append(item)
    for entity, items in entity_sources.items():
        if len(items) < 2:
            continue
        sources = set(
            item.get("source") for item in items if str(item.get("source") or "").strip()
        )
        if len(sources) < 2:
            continue
        source_thresholds: Dict[str, set[str]] = {}
        for item in items:
            src = str(item.get("source") or "").strip()
            if not src:
                continue
            content = str(item.get("content") or "")
            phrases = {
                re.sub(r"\s+", "", str(m).strip().lower())
                for m in _THRESHOLD_PHRASE_PATTERN.findall(content)
                if str(m).strip()
            }
            if phrases:
                source_thresholds.setdefault(src, set()).update(phrases)
        non_empty = [vals for vals in source_thresholds.values() if vals]
        if len(non_empty) < 2:
            continue
        unique_sets = {tuple(sorted(vals)) for vals in non_empty}
        if len(unique_sets) < 2:
            continue
        conflicts.append({
            "entity": entity,
            "sources": sorted(list(source_thresholds.keys())),
            "evidence_count": len(items),
            "conflict_type": "threshold_mismatch",
            "thresholds_by_source": {
                k: sorted(list(v)) for k, v in source_thresholds.items() if v
            },
        })
    return conflicts


def compute_confidence(
    evidence_count: int,
    conflict_count: int,
    is_degraded: bool,
) -> float:
    """计算最终答案的置信度分数。

    基于证据数量确定基础上限，根据冲突数量扣减，
    并考虑是否降级回答。

    计算逻辑：
    1. 根据证据数量查表获取基础上限
    2. 有冲突时，每条冲突扣减 _CONFLICT_PENALTY（15%）
    3. 降级回答时，上限不超过 _DEGRADED_CONFIDENCE_CAP（40%）
    4. 最终结果不低于 _MIN_CONFIDENCE（30%）

    Args:
        evidence_count: 有效证据条数，用于查表确定基础上限。
        conflict_count: 检测到的冲突数量，每条冲突扣减置信度。
        is_degraded: 是否为降级回答（如证据不足时的保守回答）。

    Returns:
        float: 最终置信度分数，范围 [0.30, 0.90]，保留两位小数。

    """
    cap = _EVIDENCE_CONFIDENCE_CAP.get(evidence_count, _DEFAULT_CONFIDENCE_CAP)
    if conflict_count > 0:
        cap = max(_MIN_CONFIDENCE, cap - conflict_count * _CONFLICT_PENALTY)
    if is_degraded:
        cap = min(cap, _DEGRADED_CONFIDENCE_CAP)
    return round(cap, 2)


def check_hop_coverage(
    traces: List[Dict[str, Any]],
    hop_count: int,
    subqueries: List[str],
) -> Tuple[float, List[str]]:
    """检查多跳问题的证据覆盖度。

    对于多跳问题（如"A导致B，B导致C，那么A和C的关系"），
    检查每个子问题或每跳是否都有对应的证据支持。

    支持两种模式：
    1. 子查询模式：检查 subqueries 列表中每个子查询是否有证据
    2. 跳数模式：检查 hop_count 指定的推理深度是否每跳都有证据

    Args:
        traces: 工具执行轨迹列表，包含 action、subquery、result、turn 等。
        hop_count: 问题跳数（推理深度），用于跳数模式。
        subqueries: 子问题列表，用于子查询模式。

    Returns:
        Tuple[float, List[str]]: (覆盖率, 未覆盖的子查询列表)
            - 覆盖率 (float): 有证据支持的子查询比例，范围 [0.0, 1.0]
            - 未覆盖列表 (List[str]): 没有证据支持的子查询文本列表
    """
    if hop_count <= 0 and not subqueries:
        return 1.0, []
    if isinstance(subqueries, list) and subqueries:
        total = len(subqueries)
        covered_subquery_ids: set[int] = set()
        text_to_id: Dict[str, int] = {
            str(subqueries[idx - 1]).strip(): idx
            for idx in range(1, total + 1)
            if str(subqueries[idx - 1]).strip()
        }
        for trace in traces:
            action = str(trace.get("action") or "")
            if action not in {"subquery_parallel_call", "subquery_dependent_call"}:
                continue
            subquery_text = str(trace.get("subquery") or "").strip()
            sid = int(text_to_id.get(subquery_text) or 0)
            if sid <= 0:
                sid = int(trace.get("subquery_id") or 0)
            if sid <= 0:
                continue
            result = trace.get("result", {})
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            payload = result.get("result", {})
            if not isinstance(payload, dict):
                continue
            has_data = False
            for key in ("records", "hits", "entities", "relationships"):
                items = payload.get(key)
                if isinstance(items, list) and len(items) > 0:
                    has_data = True
                    break
            if has_data and sid <= total:
                covered_subquery_ids.add(sid)
        covered = len(covered_subquery_ids)
        coverage = round(covered / max(total, 1), 2)
        uncovered = [
            str(subqueries[idx - 1]).strip()
            for idx in range(1, total + 1)
            if idx not in covered_subquery_ids and str(subqueries[idx - 1]).strip()
        ]
        return coverage, uncovered
    total = max(hop_count, 1)
    covered_turns = set()
    for trace in traces:
        if str(trace.get("action") or "") not in _EVIDENCE_ACTIONS:
            continue
        result = trace.get("result", {})
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        payload = result.get("result", {})
        if not isinstance(payload, dict):
            continue
        has_data = False
        for key in ("records", "hits", "entities", "relationships"):
            items = payload.get(key)
            if isinstance(items, list) and len(items) > 0:
                has_data = True
                break
        if has_data:
            covered_turns.add(trace.get("turn", 0))
    covered = min(len(covered_turns), total)
    coverage = round(covered / total, 2)
    return coverage, []


def assess_semantic_evidence_quality(
    evidence_items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """评估语义检索证据的质量。

    统计来自语义检索来源（kg_entity_relation_search、rag_search）的证据分数，
    计算最高分和平均分，并判断是否达到可信阈值。

    质量判断标准：
    - semantic_below_threshold 为 True 表示最高分低于阈值，质量不足
    - 此时可能触发回溯或降低最终置信度

    Args:
        evidence_items: 证据条目列表，每个条目包含 source、raw_score、confidence。

    Returns:
        Dict[str, Any]: 质量评估结果，包含：
            - semantic_evidence_count (int): 语义证据数量
            - semantic_score_max (float): 最高分数，保留4位小数
            - semantic_score_avg (float): 平均分数，保留4位小数
            - semantic_min_score (float): 配置的最低阈值
            - semantic_below_threshold (bool): 是否低于阈值（True 表示质量不足）
    """
    threshold = _semantic_min_score()
    semantic_scores: List[float] = []
    thresholds_used: List[float] = []
    threshold_by_source: Dict[str, float] = {}
    has_high_quality_semantic = False
    for item in evidence_items:
        source = str(item.get("source") or "").strip()
        if source not in _SEMANTIC_SOURCES:
            continue
        raw_score = item.get("raw_score")
        score = _safe_float(raw_score, -1.0)
        if score < 0:
            score = _safe_float(item.get("confidence"), 0.0)
        source_threshold = _semantic_min_score_for_source(source)
        threshold_by_source[source] = source_threshold
        semantic_scores.append(score)
        thresholds_used.append(source_threshold)
        if score >= source_threshold:
            has_high_quality_semantic = True
    semantic_count = len(semantic_scores)
    if semantic_count == 0:
        return {
            "semantic_evidence_count": 0,
            "semantic_score_max": 0.0,
            "semantic_score_avg": 0.0,
            "semantic_min_score": threshold,
            "semantic_below_threshold": False,
            "semantic_thresholds": {},
        }
    max_score = max(semantic_scores)
    avg_score = sum(semantic_scores) / semantic_count
    below_threshold = not has_high_quality_semantic
    return {
        "semantic_evidence_count": semantic_count,
        "semantic_score_max": round(max_score, 4),
        "semantic_score_avg": round(avg_score, 4),
        "semantic_min_score": round(min(thresholds_used), 4)
        if thresholds_used
        else threshold,
        "semantic_below_threshold": bool(below_threshold),
        "semantic_thresholds": {
            key: round(val, 4) for key, val in threshold_by_source.items()
        },
    }


def detect_high_risk_context(
    classification: Dict[str, Any], traces: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """检测问题是否涉及高风险医疗场景。

    扫描问题分类中的子查询、信号词，以及检索结果中的实体名称、文本内容，
    匹配预定义的高风险关键词列表（如高血压急症、脑卒中等）。

    高风险场景会触发：
    1. 降低置信度上限至 50%
    2. 在答案中添加安全警告

    Args:
        classification: 问题分类结果，包含 subqueries、signal_words 等字段。
        traces: 工具执行轨迹列表，用于扫描检索结果中的文本内容。

    Returns:
        Dict[str, Any]: 检测结果，包含：
            - high_risk (bool): 是否涉及高风险场景
            - high_risk_tokens (List[str]): 匹配到的高风险关键词列表（排序后）
    """
    user_keywords: set[str] = set()
    for item in (
        classification.get("subqueries", [])
        if isinstance(classification.get("subqueries"), list)
        else []
    ):
        text = str(item or "").strip()
        if text:
            user_keywords.add(text)
    for item in (
        classification.get("signal_words", [])
        if isinstance(classification.get("signal_words"), list)
        else []
    ):
        text = str(item or "").strip()
        if text:
            user_keywords.add(text)

    user_merged_text = "\n".join(user_keywords)

    user_matched: List[str] = []

    for token in sorted(_HIGH_RISK_KEYWORDS):
        if token in user_merged_text:
            user_matched.append(token)

    if not user_matched:
        return {
            "high_risk": False,
            "high_risk_tokens": [],
            "high_risk_user_tokens": [],
            "high_risk_evidence_tokens": [],
        }

    evidence_keywords: set[str] = set()

    for trace in traces:
        result = trace.get("result")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        payload = result.get("result")
        if not isinstance(payload, dict):
            continue
        source = str(trace.get("tool_name") or "").strip().lower()
        source_threshold = _semantic_min_score_for_source(source)
        for key in ("records", "entities", "relationships", "hits"):
            rows = payload.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows[:8]:
                if not isinstance(row, dict):
                    continue
                score = _safe_float(row.get("score"), -1.0)

                if score < 0:
                    score = _safe_float(row.get("vector_score"), -1.0)

                if score < 0:
                    properties = row.get("properties")

                    props = properties if isinstance(properties, dict) else {}

                    score = _safe_float(props.get("score"), -1.0)

                if score < source_threshold:
                    continue

                snippets = [
                    str(row.get("text") or "").strip(),
                    str(row.get("name") or "").strip(),
                    str(row.get("source_name") or "").strip(),
                    str(row.get("target_name") or "").strip(),
                ]
                for snippet in snippets:
                    if snippet:
                        evidence_keywords.add(snippet)

    evidence_merged_text = "\n".join(evidence_keywords)

    evidence_matched: List[str] = []

    for token in sorted(_HIGH_RISK_KEYWORDS):
        if token in evidence_merged_text:
            evidence_matched.append(token)

    evidence_token_set = set(evidence_matched)

    matched = [token for token in user_matched if token in evidence_token_set]

    return {
        "high_risk": bool(matched),
        "high_risk_tokens": matched,
        "high_risk_user_tokens": user_matched,
        "high_risk_evidence_tokens": evidence_matched,
    }


def extract_graphrag_quality_signals(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """提取 GraphRAG 相关的质量信号。

    从检索结果中提取实体消歧和社区发现的质量指标，
    用于评估知识图谱检索的可靠性和完整性。

    提取的信号包括：
    - 歧义实体数量：反映实体指代消解的不确定性
    - 社区数量：反映知识图谱的社区结构丰富度
    - 最高分社区分数：反映最相关社区的质量
    - 强制补充检索标志：反映是否需要额外检索

    Args:
        traces: 工具执行轨迹列表。

    Returns:
        Dict[str, Any]: 质量信号，包含：
            - disambiguation_ambiguous_count (int): 歧义实体数量
            - community_count (int): 检测到的社区数量
            - top_community_score (float): 最高分社区的分数
            - force_supplement_required (bool): 是否强制需要补充检索
    """
    ambiguous_count = 0
    community_count = 0
    top_community_score = 0.0
    force_supplement_required = False
    for trace in traces:
        if str(trace.get("action") or "") not in _EVIDENCE_ACTIONS | {"forced_supplement_call"}:
            continue
        result = trace.get("result")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        payload = result.get("result")
        if not isinstance(payload, dict):
            continue
        ambiguous_rows = payload.get("ambiguous_entities")
        if isinstance(ambiguous_rows, list):
            ambiguous_count = max(ambiguous_count, len(ambiguous_rows))
        communities = payload.get("communities")
        if isinstance(communities, list):
            community_count = max(community_count, len(communities))
        retrieval_meta = payload.get("retrieval_meta")
        if isinstance(retrieval_meta, dict):
            force_supplement_required = force_supplement_required or bool(
                retrieval_meta.get("force_supplement_required")
            )
            disambiguation = retrieval_meta.get("disambiguation")
            if isinstance(disambiguation, dict):
                ambiguous_count = max(
                    ambiguous_count, int(disambiguation.get("ambiguous_count") or 0)
                )
            community_detection = retrieval_meta.get("community_detection")
            if isinstance(community_detection, dict):
                community_count = max(
                    community_count, int(community_detection.get("community_count") or 0)
                )
                top_community_score = max(
                    top_community_score,
                    _safe_float(community_detection.get("top_community_score"), 0.0)
                )
    return {
        "disambiguation_ambiguous_count": int(ambiguous_count),
        "community_count": int(community_count),
        "top_community_score": round(top_community_score, 4),
        "force_supplement_required": bool(force_supplement_required),
    }


def _count_high_quality_items_from_payload(
    payload: Dict[str, Any], semantic_threshold: float
) -> int:
    """统计载荷中达到质量阈值的高质量条目数量。

    遍历 records、entities、relationships、hits 等各类数据，
    根据分数或匹配度判断是否达到高质量标准。

    特殊处理：
    - keyword_fallback_all_nodes 模式的弱信号不计入（关键词回退的全节点扫描）
    - records 优先使用 matched_tokens/total_tokens 比例判断
    - 其他类型使用 score 字段与阈值比较

    Args:
        payload: 工具返回的载荷数据，包含 records、entities 等字段。
        semantic_threshold: 语义相似度阈值，用于判断质量。

    Returns:
        int: 高质量条目的数量。
    """
    if not isinstance(payload, dict):
        return 0
    mode = str(payload.get("mode") or "").strip()
    if mode == "keyword_fallback_all_nodes":
        return 0
    high_count = 0
    records = payload.get("records")
    if isinstance(records, list):
        for row in records:
            if not isinstance(row, dict):
                continue
            matched = _safe_float(row.get("matched_tokens"), 0.0)
            total = _safe_float(row.get("total_tokens"), 0.0)
            if total > 0:
                if (matched / total) >= 0.5:
                    high_count += 1
                continue
            properties = row.get("properties")
            props = properties if isinstance(properties, dict) else {}
            score = _safe_float(row.get("score"), -1.0)
            if score < 0:
                score = _safe_float(props.get("score"), -1.0)
            if score >= semantic_threshold:
                high_count += 1
    entities = payload.get("entities")
    if isinstance(entities, list):
        for item in entities:
            if not isinstance(item, dict):
                continue
            score = _safe_float(item.get("score"), 0.0)
            if score >= semantic_threshold:
                high_count += 1
    relationships = payload.get("relationships")
    if isinstance(relationships, list):
        for item in relationships:
            if not isinstance(item, dict):
                continue
            score = _safe_float(item.get("score"), 0.0)
            if score >= semantic_threshold:
                high_count += 1
    hits = payload.get("hits")
    if isinstance(hits, list):
        for item in hits:
            if not isinstance(item, dict):
                continue
            score = _safe_float(item.get("score"), 0.0)
            if score >= semantic_threshold:
                high_count += 1
    return high_count


def build_subquery_evidence_bindings(
    traces: List[Dict[str, Any]],
    subqueries: List[str],
) -> List[Dict[str, Any]]:
    """构建子查询与证据的绑定关系。

    将每个子查询与对应的检索结果关联，统计每个子查询
    获得的高质量证据数量和数据来源。

    绑定逻辑：
    1. 遍历 subquery_parallel_call 和 subquery_dependent_call 动作
    2. 通过 subquery 文本或 subquery_id 匹配到对应的子查询序号
    3. 统计该子查询获得的高质量证据数量
    4. 记录证据来源工具名称（去重）

    Args:
        traces: 工具执行轨迹列表，包含 action、subquery、subquery_id、result 等。
        subqueries: 子查询文本列表，用于匹配和绑定。
    Returns:
        List[Dict[str, Any]]: 每个子查询的绑定信息，按 subquery_id 排序，每个条目包含：
            - subquery_id (int): 子查询序号（从1开始）
            - subquery (str): 子查询文本
            - high_evidence_count (int): 高质量证据数量
            - evidence_sources (List[str]): 证据来源工具列表（去重）
    """
    slots: Dict[int, Dict[str, Any]] = {}
    for idx, subquery in enumerate(subqueries, start=1):
        slots[idx] = {
            "subquery_id": idx,
            "subquery": subquery,
            "high_evidence_count": 0,
            "evidence_sources": [],
        }
    for trace in traces:
        action = str(trace.get("action") or "")
        if action not in {"subquery_parallel_call", "subquery_dependent_call"}:
            continue
        subquery_text = str(trace.get("subquery") or "").strip()
        sid = 0
        for idx, sq in enumerate(subqueries, start=1):
            if str(sq).strip() == subquery_text:
                sid = idx
                break
        if sid <= 0:
            sid = int(trace.get("subquery_id") or 0)
        if sid <= 0 or sid not in slots:
            continue
        slot = slots[sid]
        result = trace.get("result")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        payload = result.get("result")
        if not isinstance(payload, dict):
            continue
        src = str(trace.get("tool_name") or "").strip()
        source_threshold = _semantic_min_score_for_source(src)
        high_count = _count_high_quality_items_from_payload(payload, source_threshold)
        slot["high_evidence_count"] = int(slot.get("high_evidence_count") or 0) + int(high_count)
        if src and src not in slot["evidence_sources"]:
            slot["evidence_sources"].append(src)
    return [slots[idx] for idx in sorted(slots.keys())]


def _normalize_text_list(rows: Any) -> List[str]:
    """将输入列表归一化为非空字符串列表。"""
    if not isinstance(rows, list):
        return []
    output: List[str] = []
    for item in rows:
        text = str(item or "").strip()
        if text:
            output.append(text)
    return output


def _contains_any_hint(text: str, hints: Tuple[str, ...]) -> bool:
    """判断文本是否包含任一关键词。"""
    lowered = str(text or "").lower()
    if not lowered:
        return False
    for hint in hints:
        token = str(hint or "").lower().strip()
        if token and token in lowered:
            return True
    return False


def _is_axis_relevant(classification: Dict[str, Any], hints: Tuple[str, ...]) -> bool:
    """判断覆盖轴是否与当前问题相关。"""
    rows: List[str] = []
    rows.extend(
        [
            str(classification.get("primary_type") or "").strip(),
            str(classification.get("intent") or "").strip(),
        ]
    )
    rows.extend(_normalize_text_list(classification.get("types")))
    rows.extend(_normalize_text_list(classification.get("must_cover")))
    rows.extend(_normalize_text_list(classification.get("subqueries")))
    rows.extend(_normalize_text_list(classification.get("signal_words")))
    merged = "\n".join([row for row in rows if row])
    return _contains_any_hint(merged, hints)


def _is_axis_missing(missing_points: List[str], uncovered_points: List[str], hints: Tuple[str, ...]) -> bool:
    """判断覆盖轴是否出现在缺失点中。"""
    rows = [*missing_points, *uncovered_points]
    for item in rows:
        if _contains_any_hint(str(item or ""), hints):
            return True
    return False


def _build_coverage_axes(
    *,
    classification: Dict[str, Any],
    missing_points: List[str],
    uncovered_points: List[str],
) -> Dict[str, float]:
    """构建覆盖轴分数。

    仅在轴与问题语义相关时生产字段：
    - comparator 轴：相关且缺失时为 0.0，否则为 1.0
    - outcomes 轴：相关且缺失时为 0.0，否则为 1.0
    """
    output: Dict[str, float] = {}

    comparator_relevant = _is_axis_relevant(classification, _COMPARATOR_COVERAGE_HINTS)
    outcomes_relevant = _is_axis_relevant(classification, _OUTCOMES_COVERAGE_HINTS)

    if comparator_relevant:
        comparator_missing = _is_axis_missing(missing_points, uncovered_points, _COMPARATOR_COVERAGE_HINTS)
        output["coverage_comparator"] = 0.0 if comparator_missing else 1.0

    if outcomes_relevant:
        outcomes_missing = _is_axis_missing(missing_points, uncovered_points, _OUTCOMES_COVERAGE_HINTS)
        output["coverage_outcomes"] = 0.0 if outcomes_missing else 1.0

    return output


def evaluate_and_gate(
    traces: List[Dict[str, Any]],
    classification: Dict[str, Any],
    backtrack_count: int,
    max_backtrack: int,
    is_degraded: bool,
) -> Dict[str, Any]:
    """综合评估证据并执行终答门控。

    整合证据数量、冲突检测、多跳覆盖、语义质量、高风险检测、
    GraphRAG 信号等多个维度，计算最终置信度并判断是否允许生成答案。

    对于复杂问题（多跳、子查询），要求每个子问题都有足够的证据支持，
    否则触发回溯进行补充检索。

    评估维度：
    1. 证据数量与质量：基础置信度的主要决定因素
    2. 冲突检测：存在冲突时扣减置信度
    3. 多跳覆盖：复杂问题要求每跳/每子查询都有证据
    4. 语义质量：语义检索分数是否达标
    5. 高风险检测：医疗安全问题降级处理
    6. GraphRAG 信号：实体歧义和社区结构

    Args:
        traces: 工具执行轨迹列表，包含所有检索动作的结果。
        classification: 问题分类结果，包含问题类型、子查询、跳数等。
        backtrack_count: 已回溯次数，用于判断是否还能继续回溯。
        max_backtrack: 最大允许回溯次数。
        is_degraded: 是否为降级回答模式（证据不足时的保守策略）。

    Returns:
        Dict[str, Any]: 终答门控结果，包含以下字段：
            - evidence_items (List[Dict]): 证据条目列表
            - evidence_count (int): 证据总数
            - conflicts (List[Dict]): 冲突列表
            - conflict_count (int): 冲突数量
            - confidence (float): 最终置信度
            - hop_coverage (float): 多跳覆盖率 [0.0, 1.0]
            - uncovered_points (List[str]): 未覆盖的子查询列表
            - subquery_evidence_bindings (List[Dict]): 子查询证据绑定
            - insufficient_subqueries (List[Dict]): 证据不足的子查询
            - sufficient (bool): 证据是否充足
            - missing_points (List[str]): 缺失的关键点（去重后）
            - strict_subquery_binding (bool): 是否启用严格子查询绑定
            - high_risk (bool): 是否高风险场景
            - high_risk_tokens (List[str]): 高风险关键词
            - disambiguation_ambiguous_count (int): 歧义实体数
            - community_count (int): 社区数量
            - top_community_score (float): 最高分社区分数
            - force_supplement_required (bool): 是否强制补充检索
            - semantic_evidence_count (int): 语义证据数量
            - semantic_score_max (float): 语义最高分
            - semantic_score_avg (float): 语义平均分
            - semantic_min_score (float): 语义阈值
            - semantic_below_threshold (bool): 语义质量是否不足
            - should_backtrack (bool): 是否应回溯
            - gate_passed (bool): 门控是否通过（True 表示可以生成答案）
    """
    # 基础证据提取
    evidence_items = extract_evidence_items(traces)
    evidence_count = len(evidence_items)
    conflicts = detect_conflicts(evidence_items)
    conflict_count = len(conflicts)

    # 质量信号提取
    semantic_quality = assess_semantic_evidence_quality(evidence_items)
    high_risk_info = detect_high_risk_context(classification, traces)
    graphrag_quality = extract_graphrag_quality_signals(traces)

    # 多跳/子查询分析
    hop_count = classification.get("hop_count", 0)
    subqueries = classification.get("subqueries", [])
    types_raw = classification.get("types", [])
    question_types = (
        [str(item).strip().upper() for item in types_raw]
        if isinstance(types_raw, list)
        else []
    )
    # 判断是否需要严格子查询绑定：长尾、子查询、多跳问题需要
    strict_binding_required = bool(subqueries) and any(
        label in question_types for label in ["LONG_TAIL", "SUB_QUERY", "MULTI_HOP"]
    )
    hop_coverage, uncovered = check_hop_coverage(traces, hop_count, subqueries)
    subquery_bindings = build_subquery_evidence_bindings(
        traces=traces,
        subqueries=subqueries if isinstance(subqueries, list) else [],
    )
    # 证据不足的子查询：高质量证据数量为 0
    insufficient_subqueries = [
        row for row in subquery_bindings if int(row.get("high_evidence_count") or 0) < 1
    ]

    # 缺失点汇总
    missing_points: List[str] = []
    if isinstance(uncovered, list):
        missing_points.extend(
            [str(item).strip() for item in uncovered if str(item).strip()]
        )
    if isinstance(insufficient_subqueries, list):
        for row in insufficient_subqueries:
            if not isinstance(row, dict):
                continue
            subquery = str(row.get("subquery") or "").strip()
            if subquery:
                missing_points.append(subquery)
    # 去重
    dedup_missing: List[str] = []
    seen_missing: set[str] = set()
    for item in missing_points:
        if item in seen_missing:
            continue
        seen_missing.add(item)
        dedup_missing.append(item)
    missing_points = dedup_missing

    coverage_axes = _build_coverage_axes(
        classification=classification if isinstance(classification, dict) else {},
        missing_points=missing_points,
        uncovered_points=uncovered if isinstance(uncovered, list) else [],
    )

    # 充足性判断
    sufficient = evidence_count > 0 and not missing_points

    # 置信度计算
    confidence = compute_confidence(evidence_count, conflict_count, is_degraded)
    # 严格子查询绑定下，有子查询证据不足时降低上限
    if strict_binding_required and insufficient_subqueries:
        confidence = min(confidence, _INSUFFICIENT_SUBQUERY_CONFIDENCE_CAP)
    # 有未覆盖的子查询时降低上限
    if isinstance(uncovered, list) and uncovered:
        confidence = min(confidence, _UNCOVERED_SUBQUERY_CONFIDENCE_CAP)
    # 高风险场景降低上限
    if bool(high_risk_info.get("high_risk")):
        confidence = min(confidence, _HIGH_RISK_CONFIDENCE_CAP)
    # 存在实体歧义时降低上限
    if int(graphrag_quality.get("disambiguation_ambiguous_count") or 0) > 0:
        confidence = min(confidence, _AMBIGUITY_CONFIDENCE_CAP)
    # 复杂问题无社区结构时降低上限
    if (
        str(classification.get("primary_type") or "SIMPLE") != "SIMPLE"
        and int(graphrag_quality.get("community_count") or 0) <= 0
    ):
        confidence = min(confidence, _NO_COMMUNITY_CONFIDENCE_CAP)

    # 回溯判断
    should_backtrack = False
    primary_type = classification.get("primary_type", "SIMPLE")
    if primary_type != "SIMPLE":
        # 多跳覆盖不足时触发回溯
        if hop_coverage < 0.5 and backtrack_count < max_backtrack:
            should_backtrack = True
        # 证据为 0 时触发回溯
        if evidence_count == 0 and backtrack_count < max_backtrack:
            should_backtrack = True
        # 语义检索质量低于阈值时禁止终答，触发回溯
        if strict_binding_required and bool(semantic_quality.get("semantic_below_threshold")):
            should_backtrack = True
        # 子问题证据绑定不足时禁止终答，触发回溯
        if strict_binding_required and insufficient_subqueries and backtrack_count < max_backtrack:
            should_backtrack = True
        # 复杂问题下若存在实体歧义，强制补检索
        if int(graphrag_quality.get("disambiguation_ambiguous_count") or 0) > 0 and backtrack_count < max_backtrack:
            should_backtrack = True

    # 门控是否通过：证据充足且无需回溯
    gate_passed = sufficient and (not should_backtrack)

    return {
        "evidence_items": evidence_items,
        "evidence_count": evidence_count,
        "conflicts": conflicts,
        "conflict_count": conflict_count,
        "confidence": confidence,
        "hop_coverage": hop_coverage,
        "uncovered_points": uncovered,
        "subquery_evidence_bindings": subquery_bindings,
        "insufficient_subqueries": insufficient_subqueries,
        "sufficient": sufficient,
        "missing_points": missing_points,
        "strict_subquery_binding": strict_binding_required,
        **coverage_axes,
        **high_risk_info,
        **graphrag_quality,
        **semantic_quality,
        "should_backtrack": should_backtrack,
        "gate_passed": gate_passed,
    }


