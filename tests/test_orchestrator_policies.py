import unittest

from app.a2a.models import EvidenceBundle
from app.a2a.orchestrator.policies import build_final_answer, decide_need_supplement


class TestOrchestratorPolicies(unittest.TestCase):
    def test_comparison_question_should_request_rag_supplement(self) -> None:
        bundle = EvidenceBundle(
            kg_evidence=[
                {
                    "tool": "kg_entity_relation_search",
                    "entity": {"name": "单片复方制剂", "label": "TreatmentPlan", "text": "用于高血压治疗"},
                }
            ],
            rag_evidence=[],
            citations=[],
        )
        decision = decide_need_supplement(
            evidence_bundle=bundle,
            round_index=1,
            max_rounds=2,
            last_agent="kg",
            last_no_signal=False,
            original_question="贝那普利氨氯地平片与氨氯地平单药相比有什么优势？",
        )
        self.assertTrue(decision.need_supplement)
        self.assertEqual(decision.next_agent, "rag")
        self.assertEqual(decision.reason, "comparison_question_need_rag")

    def test_final_answer_should_not_dump_raw_dict(self) -> None:
        bundle = EvidenceBundle(
            kg_evidence=[
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
            rag_evidence=[],
            citations=[],
        )
        answer = build_final_answer(
            evidence_bundle=bundle,
            question="贝那普利氨氯地平片与氨氯地平单药相比有什么优势？",
            trace=[],
        )
        self.assertIn("图谱要点：", answer.answer)
        self.assertNotIn("{'doc_id'", answer.answer)


if __name__ == "__main__":
    unittest.main()
