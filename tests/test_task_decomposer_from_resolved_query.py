"""ResolvedQuery 拆解器单元测试。"""

import unittest

from app.a2a.models import SubAgentConstraints
from app.a2a.orchestrator.task_decomposer import decompose_to_tasks


class TestTaskDecomposerFromResolvedQuery(unittest.TestCase):
    def test_should_keep_kg_first_for_source_question(self) -> None:
        resolved_query = {
            "standalone_question": "老年高血压患者 ACEI 和 ARB 联用是否推荐，原文依据是什么？",
            "intent": "evidence_verification",
            "question_mode": "medical_new_question",
            "subqueries": [
                "老年高血压患者 ACEI 和 ARB 联用是否推荐",
                "相关指南原文依据是什么",
            ],
            "must_cover": ["target_population", "recommendation", "evidence_source"],
            "entities": ["老年高血压", "ACEI", "ARB"],
            "filters": ["指南语境"],
            "preferred_sources": ["kg", "rag"],
            "carry_over_context": ["持续追问联合用药"],
        }
        tasks = decompose_to_tasks(
            resolved_query,
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].target_agent, "kg")
        self.assertEqual(tasks[1].target_agent, "kg")
        self.assertIn("target_population", tasks[0].must_cover)
        self.assertIn("老年高血压", tasks[0].entities)

    def test_should_fallback_to_single_task_when_subqueries_empty(self) -> None:
        resolved_query = {
            "standalone_question": "那老年人呢",
            "intent": "direct_answer",
            "question_mode": "medical_new_question",
            "subqueries": [],
            "must_cover": [],
            "entities": [],
            "filters": [],
            "preferred_sources": ["kg"],
            "carry_over_context": [],
        }
        tasks = decompose_to_tasks(
            resolved_query,
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].question, "那老年人呢")
        self.assertEqual(tasks[0].target_agent, "kg")

    def test_should_keep_kg_first_for_source_like_subquery_with_kg_preference(self) -> None:
        resolved_query = {
            "standalone_question": "ACEI/ARB 联合用药依据是什么",
            "intent": "evidence_verification",
            "question_mode": "medical_new_question",
            "subqueries": ["相关指南原文依据是什么"],
            "must_cover": ["recommendation", "evidence_source"],
            "entities": ["ACEI", "ARB"],
            "filters": ["指南语境"],
            "preferred_sources": ["kg"],
            "carry_over_context": [],
        }
        tasks = decompose_to_tasks(
            resolved_query,
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=2),
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].target_agent, "kg")
        self.assertEqual(tasks[0].tool_name, "kg_entity_relation_search")

    def test_non_medical_question_mode_should_skip_task_decompose(self) -> None:
        resolved_query = {
            "standalone_question": "你能做什么",
            "intent": "direct_answer",
            "question_mode": "capability_query",
            "subqueries": ["你能做什么"],
            "must_cover": ["answer"],
            "entities": [],
            "filters": [],
            "preferred_sources": ["capability"],
            "carry_over_context": [],
        }
        tasks = decompose_to_tasks(
            resolved_query,
            SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=1),
        )
        self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
