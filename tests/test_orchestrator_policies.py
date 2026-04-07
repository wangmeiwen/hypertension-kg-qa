"""test_orchestrator_policies 模块。"""

import unittest

from app.a2a.models import SubAgentConstraints, SubTask
from app.a2a.orchestrator.answer_gate import compose_final_answer
from app.a2a.orchestrator.supplement_decider import decide_supplement


class TestOrchestratorPolicies(unittest.TestCase):
    def test_no_signal_first_round_should_refine_instead_of_reroute(self) -> None:
        """首轮无信号应先 query_refine，不直接跨代理。"""
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="贝那普利氨氯地平片与氨氯地平单药相比有什么优势？",
            intent="comparison",
            evidence_type_required=["relation", "text_quote"],
            must_cover=["intervention", "comparator", "outcomes"],
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )
        decision = decide_supplement(
            task=task,
            round_index=1,
            max_rounds=2,
            coverage={"coverage_status": "none", "missing_points": ["outcomes"]},
            last_agent="kg",
            last_no_signal=True,
        )
        self.assertTrue(decision.need_supplement)
        self.assertEqual(decision.supplement_type, "query_refine")
        self.assertEqual(decision.next_agent, "kg")
        self.assertEqual(decision.reason, "coverage_gap_refine")

    def test_final_answer_should_not_dump_raw_dict(self) -> None:
        """最终答案不应直接泄露原始字典字符串。"""
        answer = compose_final_answer(
            question="贝那普利氨氯地平片与氨氯地平单药相比有什么优势？",
            evidence_bundle={
                "kg_evidence": [
                    {
                        "tool": "kg_entity_relation_search",
                        "entity": {
                            "doc_id": "x1",
                            "name": "单片复方制剂",
                            "label": "TreatmentPlan",
                            "text": "实体 单片复方制剂，类型 TreatmentPlan。",
                        },
                    }
                ],
                "rag_evidence": [],
                "citations": [],
            },
            gate_result={"hop_coverage": 0.8},
            answerability={"answer_mode": "direct", "confidence_cap": 0.8, "direct_support": True, "evidence_count": 1},
            trace=[],
            answer_text="结论：当前证据可支持回答。\n证据支持点：已有相关实体证据。\n不确定性与适用边界：仍需结合具体人群。",
        )
        self.assertIn("结论：", answer.answer)
        self.assertNotIn("{'doc_id'", answer.answer)
        self.assertIn("免责声明：", answer.answer)

    def test_low_value_partial_should_escalate_to_rag_on_second_round(self) -> None:
        """comparison 在第二轮 low-signal 条件下应触发 KG->RAG 升级。"""
        task = SubTask(
            subtask_id=1,
            target_agent="kg",
            question="比较复方与单药的疗效和安全性差异",
            intent="comparison",
            evidence_type_required=["relation", "text_quote"],
            must_cover=["疗效差异", "安全性差异"],
            constraints=SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )
        decision = decide_supplement(
            task=task,
            round_index=2,
            max_rounds=2,
            coverage={
                "coverage_status": "partial",
                "missing_points": ["疗效差异"],
                "binding_insufficient": True,
                "high_evidence_count": 0,
                "low_signal": True,
                "evidence_count": 3,
            },
            last_agent="kg",
            last_no_signal=False,
        )
        self.assertTrue(decision.need_supplement)
        self.assertEqual(decision.supplement_type, "reroute")
        self.assertEqual(decision.next_agent, "rag")
        self.assertEqual(decision.reason, "kg_to_rag_escalation")


if __name__ == "__main__":
    unittest.main()
