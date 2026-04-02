"""门控/覆盖/改写链路回归测试。"""

import unittest

from app.a2a.agent.gating_mechanism.evidence_evaluator import evaluate_and_gate
from app.a2a.models import SubAgentConstraints, SubTask
from app.a2a.orchestrator.answer_gate import assess_answerability, compose_final_answer
from app.a2a.orchestrator.coverage import evaluate_task_coverage
from app.a2a.orchestrator.retrieval_planner import plan_task
from app.a2a.orchestrator.router import select_agent
from app.a2a.orchestrator.task_decomposer import decompose_to_tasks
from app.a2a.subagents.kg.query_builder import build_kg_entity_relation_payload
from app.a2a.subagents.rag.query_builder import build_rag_payload


class TestOrchestratorAlignmentRegressions(unittest.TestCase):
    def test_run_gate_should_require_sufficient_for_gate_passed(self) -> None:
        gate_result = evaluate_and_gate(
            traces=[],
            classification={
                "primary_type": "SIMPLE",
                "subqueries": [],
                "types": [],
                "signal_words": [],
            },
            backtrack_count=0,
            max_backtrack=2,
            is_degraded=False,
        )
        self.assertFalse(bool(gate_result.get("sufficient")))
        self.assertFalse(bool(gate_result.get("gate_passed")))

    def test_should_backtrack_should_override_gate_passed_flag(self) -> None:
        answerability = assess_answerability(
            evidence_bundle={"kg_evidence": [], "rag_evidence": []},
            gate_result={
                "should_backtrack": True,
                "gate_passed": True,
                "sufficient": True,
                "confidence": 0.9,
                "missing_points": ["outcomes"],
                "evidence_count": 0,
            },
            coverage_map={"1": {"direct_support": True}},
        )
        self.assertFalse(bool(answerability.get("gate_passed")))
        self.assertEqual(str(answerability.get("answer_mode")), "none")

    def test_reason_should_not_claim_gate_passed_when_backtrack_required(self) -> None:
        answerability = assess_answerability(
            evidence_bundle={"kg_evidence": [{"entity": {"name": "A"}}], "rag_evidence": []},
            gate_result={
                "should_backtrack": True,
                "gate_passed": True,
                "sufficient": True,
                "confidence": 0.9,
                "evidence_count": 1,
            },
            coverage_map={"1": {"direct_support": True}},
        )
        self.assertFalse(bool(answerability.get("gate_passed")))
        self.assertEqual(str(answerability.get("reason") or ""), "gate_failed_backtrack_required")

    def test_reason_should_not_claim_gate_passed_when_not_sufficient(self) -> None:
        answerability = assess_answerability(
            evidence_bundle={"kg_evidence": [{"entity": {"name": "A"}}], "rag_evidence": []},
            gate_result={
                "should_backtrack": False,
                "gate_passed": True,
                "sufficient": False,
                "confidence": 0.9,
                "evidence_count": 1,
            },
            coverage_map={"1": {"direct_support": False}},
        )
        self.assertFalse(bool(answerability.get("gate_passed")))
        self.assertEqual(str(answerability.get("reason") or ""), "gate_failed_not_sufficient")

    def test_gate_passed_should_not_output_insufficient(self) -> None:
        answerability = assess_answerability(
            evidence_bundle={"kg_evidence": [{"entity": {"name": "A"}}], "rag_evidence": []},
            gate_result={
                "should_backtrack": False,
                "gate_passed": True,
                "sufficient": True,
                "confidence": 0.8,
                "missing_points": [],
                "evidence_count": 2,
            },
            coverage_map={"1": {"direct_support": False}},
        )
        self.assertNotEqual(str(answerability.get("answer_mode")), "none")
        self.assertEqual(str(answerability.get("answer_mode")), "partial")

    def test_answerability_should_reject_gate_passed_when_not_sufficient(self) -> None:
        answerability = assess_answerability(
            evidence_bundle={"kg_evidence": [], "rag_evidence": []},
            gate_result={
                "should_backtrack": False,
                "gate_passed": True,
                "sufficient": False,
                "confidence": 0.9,
                "missing_points": ["outcomes"],
                "evidence_count": 0,
            },
            coverage_map={"1": {"direct_support": True}},
        )
        self.assertFalse(bool(answerability.get("gate_passed")))
        self.assertEqual(str(answerability.get("answer_mode")), "none")

    def test_answer_mode_should_follow_final_evidence_pool_not_gate_counter(self) -> None:
        answerability = assess_answerability(
            evidence_bundle={"kg_evidence": [{"entity": {"name": "A"}}], "rag_evidence": []},
            gate_result={
                "should_backtrack": False,
                "gate_passed": True,
                "sufficient": True,
                "confidence": 0.7,
                "evidence_count": 0,
            },
            coverage_map={"1": {"direct_support": False}},
        )
        self.assertEqual(str(answerability.get("answer_mode")), "partial")
        self.assertEqual(int(answerability.get("final_evidence_count") or -1), 1)

    def test_final_answer_should_use_final_evidence_count(self) -> None:
        final = compose_final_answer(
            question="测试问题",
            evidence_bundle={"kg_evidence": [{"entity": {"name": "A"}}], "rag_evidence": [], "citations": []},
            gate_result={"evidence_count": 0},
            answerability={"answer_mode": "partial", "confidence_cap": 0.6, "direct_support": False},
            trace=[],
            answer_text="结论：当前证据支持的部分可回答。\n证据支持点：A。\n不确定性与适用边界：目前不能直接下结论的部分仍需补充证据。",
        )
        summary = final.evidence_summary if isinstance(final.evidence_summary, dict) else {}
        self.assertEqual(int(summary.get("evidence_count")), 1)
        self.assertEqual(int(summary.get("kg_evidence_count") or -1), 1)

    def test_coverage_axes_should_be_omitted_when_not_produced(self) -> None:
        final = compose_final_answer(
            question="测试问题",
            evidence_bundle={"kg_evidence": [{"entity": {"name": "A"}}], "rag_evidence": [], "citations": []},
            gate_result={"hop_coverage": 1.0},
            answerability={"answer_mode": "direct", "confidence_cap": 0.8, "direct_support": True},
            trace=[],
            answer_text="结论：可回答。\n证据支持点：A。\n不确定性与适用边界：需结合场景。",
        )
        summary = final.evidence_summary if isinstance(final.evidence_summary, dict) else {}
        self.assertNotIn("coverage_comparator", summary)
        self.assertNotIn("coverage_outcomes", summary)

    def test_gate_result_should_produce_coverage_axes_for_comparison_like_query(self) -> None:
        gate_result = evaluate_and_gate(
            traces=[],
            classification={
                "primary_type": "SIMPLE",
                "intent": "comparison",
                "types": ["COMPARISON"],
                "must_cover": ["comparator", "outcomes"],
                "subqueries": ["比较A与B在疗效结局上的差异"],
                "signal_words": [],
            },
            backtrack_count=0,
            max_backtrack=2,
            is_degraded=False,
        )
        self.assertIn("coverage_comparator", gate_result)
        self.assertIn("coverage_outcomes", gate_result)
        self.assertEqual(float(gate_result.get("coverage_comparator") or 0.0), 0.0)
        self.assertEqual(float(gate_result.get("coverage_outcomes") or 0.0), 0.0)

    def test_relation_task_should_not_require_source_cover_point(self) -> None:
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="比较方案",
            intent="comparison",
            entities=[],
            dependency_on=[],
            evidence_type_required=["relation"],
            must_cover=["intervention", "evidence_source"],
            priority=1,
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=1),
            query_terms=["intervention", "benefit"],
        )
        coverage = evaluate_task_coverage(
            task,
            [{"hit": {"text": "intervention benefit"}}],
        )
        expected_points = coverage.get("expected_points") if isinstance(coverage.get("expected_points"), list) else []
        self.assertEqual(expected_points, ["intervention"])
        self.assertEqual(str(coverage.get("coverage_status")), "strong")

    def test_kg_query_builder_should_inject_refine_terms(self) -> None:
        payload = build_kg_entity_relation_payload(
            "原始问题",
            arguments={"refine_terms": ["用药", "剂量"]},
            query_terms=["高血压", "用药", "剂量"],
        )
        self.assertEqual(str(payload.get("query")), "原始问题 用药 剂量")

        base_payload = build_kg_entity_relation_payload(
            "原始问题",
            arguments={},
            query_terms=["高血压", "用药", "剂量"],
        )
        self.assertEqual(str(base_payload.get("query")), "原始问题")

    def test_invalid_intent_should_be_normalized(self) -> None:
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="测试",
            intent="comaprison",
            entities=[],
            dependency_on=[],
            evidence_type_required=["relation"],
            must_cover=["answer"],
            priority=1,
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=1),
        )
        planned = plan_task(task)
        self.assertEqual(planned.intent, "comparison")
        retry_context = planned.retry_context if isinstance(planned.retry_context, dict) else {}
        self.assertEqual(str(retry_context.get("intent_normalized_from") or ""), "comaprison")

    def test_chinese_intent_should_be_normalized_to_comparison(self) -> None:
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="测试",
            intent="比较ACEI/CCB复方制剂与CCB单药在高血压治疗中的疗效差异和靶器官保护作用",
            entities=[],
            dependency_on=[],
            evidence_type_required=["relation"],
            must_cover=["answer"],
            priority=1,
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=1),
        )
        planned = plan_task(task)
        self.assertEqual(planned.intent, "comparison")

    def test_comparison_with_dual_evidence_should_keep_decompose_target(self) -> None:
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="比较ACEI与ARB在肾保护方面差异",
            intent="comparison",
            entities=[],
            dependency_on=[],
            evidence_type_required=["relation", "text_quote"],
            must_cover=["answer"],
            priority=1,
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=1),
        )
        planned = plan_task(task)
        self.assertEqual(planned.target_agent, "kg")
        self.assertEqual(planned.tool_name, "kg_entity_relation_search")
        selected = select_agent(planned, {}, [], {})
        self.assertEqual(selected, "kg")

    def test_comparison_with_preferred_kg_first_round_should_use_kg_tool(self) -> None:
        tasks = decompose_to_tasks(
            {
                "standalone_question": "比较ACEI与ARB在肾保护上的差异",
                "intent": "comparison",
                "subqueries": ["比较ACEI与ARB在肾保护上的差异"],
                "must_cover": ["肾保护", "evidence_source"],
                "entities": ["ACEI", "ARB"],
                "filters": [],
                "preferred_sources": ["kg"],
            },
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )
        self.assertEqual(len(tasks), 1)
        planned = plan_task(tasks[0])
        selected = select_agent(planned, {}, [], {"target_agent": "kg", "no_signal": False})
        self.assertEqual(selected, "kg")
        self.assertEqual(planned.tool_name, "kg_entity_relation_search")

    def test_task_specific_must_cover_should_split_by_subquery(self) -> None:
        tasks = decompose_to_tasks(
            {
                "standalone_question": "比较复方与单药在疗效与安全性差异，并给出证据来源",
                "intent": "comparison",
                "subqueries": ["比较疗效与靶器官保护作用", "给出证据来源与原文依据"],
                "must_cover": ["疗效差异", "靶器官保护", "evidence_source"],
                "entities": ["ACEI", "CCB"],
                "filters": [],
                "preferred_sources": ["kg", "rag"],
            },
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )
        self.assertEqual(len(tasks), 2)
        self.assertNotEqual(tasks[0].must_cover, tasks[1].must_cover)
        self.assertIn("evidence_source", tasks[1].must_cover)
        self.assertNotIn("evidence_source", tasks[0].must_cover)

    def test_must_cover_should_not_fallback_to_first_two_points_for_safety_subquery(self) -> None:
        tasks = decompose_to_tasks(
            {
                "standalone_question": "比较ACEI/CCB复方与CCB单药在不同维度的差异",
                "intent": "comparison",
                "subqueries": ["safety difference"],
                "must_cover": ["bp reduction", "renal protection", "safety difference", "evidence_source"],
                "entities": ["ACEI", "CCB"],
                "filters": [],
                "preferred_sources": ["kg", "rag"],
            },
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )
        self.assertEqual(len(tasks), 1)
        self.assertIn("safety difference", tasks[0].must_cover)
        self.assertNotEqual(tasks[0].must_cover, ["bp reduction", "renal protection"])

    def test_chinese_safety_subquery_with_efficacy_only_must_cover_should_not_inherit_all_points(self) -> None:
        tasks = decompose_to_tasks(
            {
                "standalone_question": "复方制剂与单药联合用药的安全性特征和不良反应差异",
                "intent": "comparison",
                "subqueries": ["复方制剂与单药联合用药的安全性特征和不良反应差异"],
                "must_cover": ["降压效果", "肾脏保护", "心脏保护", "血管保护", "机制互补"],
                "entities": ["复方制剂", "单药联合用药"],
                "filters": [],
                "preferred_sources": ["kg"],
            },
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].must_cover, [])
        retry_context = tasks[0].retry_context if isinstance(tasks[0].retry_context, dict) else {}
        self.assertEqual(str(retry_context.get("must_cover_mapping") or ""), "unmapped")

    def test_unmapped_empty_expected_points_should_not_be_strong(self) -> None:
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="复方制剂安全性差异",
            intent="comparison",
            entities=[],
            dependency_on=[],
            evidence_type_required=["relation"],
            must_cover=[],
            priority=1,
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=1),
            query_terms=["安全", "差异"],
            retry_context={"must_cover_mapping": "unmapped"},
        )
        coverage = evaluate_task_coverage(task, [{"hit": {"text": "该方案在安全和差异方面有描述"}}])
        self.assertEqual(str(coverage.get("coverage_status") or ""), "partial")
        self.assertEqual(float(coverage.get("coverage_ratio", -1)), 0.0)

    def test_rag_payload_should_append_refine_terms_without_dropping_question(self) -> None:
        payload = build_rag_payload(
            "原始RAG问题",
            arguments={"refine_terms": ["指南", "原文"]},
            query_terms=["高血压", "治疗"],
        )
        self.assertEqual(str(payload.get("query")), "原始RAG问题 指南 原文")

        base_payload = build_rag_payload(
            "原始RAG问题",
            arguments={},
            query_terms=["高血压", "治疗"],
        )
        self.assertEqual(str(base_payload.get("query")), "原始RAG问题")


if __name__ == "__main__":
    unittest.main()
