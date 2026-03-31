"""
A2A agent/gating_mechanism/__init__.py 模块。

本模块提供 __init__ 相关实现。
"""

from .evidence_evaluator import evaluate_and_gate
from .evidence_trace_utils import (
	_build_retrieved_corpus_text,
	_collect_community_report_lines,
	_collect_community_report_rows,
	_collect_db_balance_status,
	_freeze_args,
	_remove_ungrounded_claim_lines,
	_result_has_signal,
)

__all__ = [
	"evaluate_and_gate",
	"_build_retrieved_corpus_text",
	"_collect_community_report_lines",
	"_collect_community_report_rows",
	"_remove_ungrounded_claim_lines",
	"_freeze_args",
	"_result_has_signal",
	"_collect_db_balance_status",
]
