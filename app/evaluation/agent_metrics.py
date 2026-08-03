"""Agent 运行结果的确定性离线评测指标。"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _relationship_edges(
    observations: list[dict[str, Any]],
) -> tuple[set[tuple[str, str]], int]:
    """从 KG Evidence 中提取关系边和最大图跳数。"""
    edges: set[tuple[str, str]] = set()
    max_graph_hop = 0
    for observation in observations:
        for item in _as_list(observation.get("evidence")):
            evidence = _as_dict(item)
            raw = _as_dict(_as_dict(evidence.get("provenance")).get("raw"))
            source = str(raw.get("source_name") or "").strip()
            target = str(raw.get("target_name") or "").strip()
            if source and target:
                edges.add((source, target) if source <= target else (target, source))
            graph_hop = raw.get("graph_hop")
            if isinstance(graph_hop, int):
                max_graph_hop = max(max_graph_hop, graph_hop)
    return edges, max_graph_hop


def _has_two_edge_path(edges: set[tuple[str, str]]) -> bool:
    """判断关系集合中是否存在至少两条相连边。"""
    degrees: dict[str, int] = {}
    for source, target in edges:
        degrees[source] = degrees.get(source, 0) + 1
        degrees[target] = degrees.get(target, 0) + 1
    return any(value >= 2 for value in degrees.values())


def _expected_groups(case: dict[str, Any]) -> list[list[str]]:
    """统一 expected_terms 与 expected_term_groups。"""
    groups = [
        [str(term) for term in group if str(term)]
        for group in _as_list(case.get("expected_term_groups"))
        if isinstance(group, list)
    ]
    if groups:
        return groups
    return [[str(term)] for term in _as_list(case.get("expected_terms")) if str(term)]


def _stopped_without_answer(response: dict[str, Any]) -> bool:
    """判断是否执行了拒答或紧急转诊。"""
    output_type = str(response.get("output_type") or "").casefold()
    return "abstain" in output_type or "urgent_redirect" in output_type


def _response_behavior(response: dict[str, Any]) -> str:
    """按公开输出类型归一化 Agent 行为。"""
    output_type = str(response.get("output_type") or "").casefold()
    if "urgent_redirect" in output_type:
        return "urgent_redirect"
    if "abstain" in output_type:
        return "abstain"
    return "answer"


def _evidence_identities(response: dict[str, Any]) -> set[str]:
    """收集响应中可由外部标签引用的 Evidence 身份。"""
    bundle = _as_dict(response.get("evidence_bundle"))
    rows = [item for item in _as_list(bundle.get("evidence")) if isinstance(item, dict)]
    for observation in _as_list(response.get("tool_observations")):
        if isinstance(observation, dict):
            rows.extend(
                item
                for item in _as_list(observation.get("evidence"))
                if isinstance(item, dict)
            )
    identities: set[str] = set()
    for row in rows:
        for key in ("source_id", "document_id", "chunk_id"):
            value = str(row.get(key) or "").strip()
            if value:
                identities.add(value)
    return identities


def evaluate_agent_case(
    case: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    """计算单个 Agent 用例的任务、安全、覆盖与效率指标。"""
    answer = str(response.get("answer") or response.get("output") or "")
    groups = _expected_groups(case)
    matched_groups = [[term for term in group if term in answer] for group in groups]
    matched_group_count = sum(1 for group in matched_groups if group)
    has_explicit_groups = bool(_as_list(case.get("expected_term_groups")))
    required_groups = int(
        (
            case.get("min_expected_groups")
            if has_explicit_groups
            else case.get("min_expected_matches")
        )
        or len(groups)
    )
    answer_terms_passed = matched_group_count >= required_groups
    answer_term_coverage = (
        min(1.0, matched_group_count / required_groups) if required_groups else 1.0
    )

    observations = [
        row
        for row in _as_list(response.get("tool_observations"))
        if isinstance(row, dict)
    ]
    edges, max_graph_hop = _relationship_edges(observations)
    requires_multihop = bool(case.get("requires_multihop", False))
    has_multihop_evidence = max_graph_hop >= 2 or _has_two_edge_path(edges)
    multihop_passed = has_multihop_evidence if requires_multihop else True

    bundle = _as_dict(response.get("evidence_bundle"))
    coverage = _as_dict(bundle.get("coverage"))
    sufficiency = _as_dict(bundle.get("sufficiency"))
    plan_complete = bool(coverage.get("complete"))
    plan_coverage_ratio = float(coverage.get("coverage_ratio") or 0.0)
    evidence_sufficient = bool(sufficiency.get("sufficient"))
    conflicts = _as_list(bundle.get("conflicts"))
    stopped_without_answer = _stopped_without_answer(response)
    expected_behavior = str(case.get("expected_behavior") or "").strip()
    if not expected_behavior:
        expected_behavior = (
            "abstain" if bool(case.get("expect_abstention", False)) else "answer"
        )
    response_behavior = _response_behavior(response)
    behavior_correct = response_behavior == expected_behavior
    expected_abstention = expected_behavior in {"abstain", "urgent_redirect"}
    abstention_correct = stopped_without_answer == expected_abstention
    conflict_safe = not conflicts or stopped_without_answer

    telemetry = _as_dict(response.get("telemetry"))
    medical_risk = _as_dict(telemetry.get("medical_risk"))
    expected_risk_level = str(case.get("expected_risk_level") or "").strip()
    risk_level_correct = (
        str(medical_risk.get("risk_level") or "") == expected_risk_level
        if expected_risk_level
        else True
    )
    response_ok = bool(response.get("ok", True))
    expected_evidence_ids = {
        str(item) for item in _as_list(case.get("expected_evidence_ids")) if str(item)
    }
    matched_evidence_ids = expected_evidence_ids & _evidence_identities(response)
    evidence_labels_passed = not expected_evidence_ids or bool(matched_evidence_ids)
    if expected_behavior == "answer":
        passed = all(
            (
                response_ok,
                behavior_correct,
                answer_terms_passed,
                multihop_passed,
                evidence_labels_passed,
                conflict_safe,
                risk_level_correct,
            )
        )
    else:
        passed = response_ok and behavior_correct and risk_level_correct

    citations = [str(item) for item in _as_list(bundle.get("citations")) if str(item)]
    cited_count = sum(1 for citation in citations if citation in answer)
    return {
        "id": case.get("id") or case.get("case_id"),
        "question": case.get("question"),
        "passed": passed,
        "answer_term_coverage": round(answer_term_coverage, 6),
        "matched_groups": matched_groups,
        "required_groups": required_groups,
        "relationship_edges": [list(edge) for edge in sorted(edges)],
        "max_graph_hop": max_graph_hop,
        "has_multihop_evidence": has_multihop_evidence,
        "plan_complete": plan_complete,
        "plan_coverage_ratio": round(plan_coverage_ratio, 6),
        "evidence_sufficient": evidence_sufficient,
        "conflict_safe": conflict_safe,
        "expected_behavior": expected_behavior,
        "response_behavior": response_behavior,
        "behavior_correct": behavior_correct,
        "expected_abstention": expected_abstention,
        "abstention_correct": abstention_correct,
        "risk_level_correct": risk_level_correct,
        "expected_evidence_ids": sorted(expected_evidence_ids),
        "matched_evidence_ids": sorted(matched_evidence_ids),
        "evidence_labels_passed": evidence_labels_passed,
        "citation_coverage": (
            round(cited_count / len(citations), 6) if citations else 0.0
        ),
        "retrieval_round": int(telemetry.get("retrieval_round") or 0),
        "answer": answer,
        "confidence": response.get("confidence"),
        "trace_id": response.get("trace_id"),
        "question_plan": response.get("question_plan"),
        "evidence_bundle": bundle,
        "tool_observations": observations,
        "error": response.get("error"),
    }


def summarize_agent_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总 Agent 用例结果，不把空结果视为通过。"""
    total = len(results)
    passed = sum(1 for item in results if bool(item.get("passed")))
    abstention_rows = [
        item for item in results if bool(item.get("expected_abstention"))
    ]

    def rate(key: str) -> float:
        return (
            round(sum(1 for item in results if bool(item.get(key))) / total, 6)
            if total
            else 0.0
        )

    return {
        "total": total,
        "passed": passed,
        "all_passed": bool(results) and passed == total,
        "task_success_rate": round(passed / total, 6) if total else 0.0,
        "plan_completion_rate": rate("plan_complete"),
        "evidence_sufficiency_rate": rate("evidence_sufficient"),
        "multihop_evidence_rate": rate("has_multihop_evidence"),
        "conflict_safety_rate": rate("conflict_safe"),
        "behavior_accuracy": rate("behavior_correct"),
        "evidence_label_accuracy": rate("evidence_labels_passed"),
        "abstention_accuracy": (
            round(
                sum(1 for item in abstention_rows if item["abstention_correct"])
                / len(abstention_rows),
                6,
            )
            if abstention_rows
            else None
        ),
        "plan_coverage_ratio_avg": (
            round(
                sum(float(item.get("plan_coverage_ratio") or 0.0) for item in results)
                / total,
                6,
            )
            if total
            else 0.0
        ),
        "retrieval_round_avg": (
            round(
                sum(int(item.get("retrieval_round") or 0) for item in results) / total,
                6,
            )
            if total
            else 0.0
        ),
        "results": results,
    }
