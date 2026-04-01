"""任务覆盖度评估模块。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import SubTask


def _evidence_text(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""
    entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
    if entity:
        return str(entity.get("text") or entity.get("name") or "").strip()
    relation = row.get("relationship") if isinstance(row.get("relationship"), dict) else {}
    if relation:
        src = str(relation.get("source_name") or "").strip()
        rel = str(relation.get("relation_type") or "").strip()
        tgt = str(relation.get("target_name") or "").strip()
        text = str(relation.get("text") or "").strip()
        return f"{src} {rel} {tgt} {text}".strip()
    hit = row.get("hit") if isinstance(row.get("hit"), dict) else {}
    if hit:
        return str(hit.get("text") or hit.get("parent_content") or hit.get("content") or "").strip()
    record = row.get("record") if isinstance(row.get("record"), dict) else {}
    if record:
        return str(record)
    return ""


def evaluate_task_coverage(task: SubTask, evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    must_cover = [str(item or "").strip() for item in task.must_cover if str(item or "").strip()]
    texts = [_evidence_text(row) for row in evidence_rows if _evidence_text(row)]

    covered: list[str] = []
    for point in must_cover:
        if any(point in text for text in texts):
            covered.append(point)

    direct_support = False
    if task.query_terms:
        for text in texts:
            hit_count = sum(1 for token in task.query_terms if token and token in text)
            if hit_count >= 2:
                direct_support = True
                break

    coverage_ratio = float(len(covered)) / float(len(must_cover)) if must_cover else 1.0
    missing = [point for point in must_cover if point not in covered]

    if coverage_ratio >= 1.0 and direct_support:
        status = "strong"
    elif coverage_ratio > 0.0 or bool(texts):
        status = "partial"
    else:
        status = "none"

    return {
        "coverage_status": status,
        "coverage_ratio": round(coverage_ratio, 4),
        "covered_points": covered,
        "missing_points": missing,
        "direct_support": direct_support,
        "evidence_count": len(evidence_rows),
    }
