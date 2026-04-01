"""会话记忆 Resolver/Summarizer Prompt 常量。"""

RESOLVED_QUERY_SCHEMA_TEXT = """
{
  "standalone_question": "string",
  "intent": "string",
  "subqueries": ["string"],
  "must_cover": ["string"],
  "entities": ["string"],
  "filters": ["string"],
  "preferred_sources": ["kg|rag"],
  "carry_over_context": ["string"]
}
""".strip()

SESSION_SUMMARY_SCHEMA_TEXT = """
{
  "version": 1,
  "last_message_id": 0,
  "conversation_goal": "string",
  "confirmed_facts": ["string"],
  "working_assumptions": ["string"],
  "answered_points": ["string"],
  "open_questions": ["string"],
  "risk_notes": ["string"]
}
""".strip()

RETRIEVAL_SUMMARY_SCHEMA_TEXT = """
{
  "version": 1,
  "last_message_id": 0,
  "focus_entities": ["string"],
  "covered_topics": ["string"],
  "evidence_gaps": ["string"],
  "recommended_queries": ["string"],
  "retrieval_risk_flags": ["string"]
}
""".strip()

RESOLVE_QUERY_SYSTEM_PROMPT = """
你是会话问题改写器，只负责把当前问题补全为独立问题，不负责回答。
你必须只基于提供的历史消息与会话记忆，禁止编造患者条件。
输出必须是严格 JSON，对应如下 schema：
{resolved_query_schema}
即使上下文不足，也必须返回最小合法结构。
""".strip().format(resolved_query_schema=RESOLVED_QUERY_SCHEMA_TEXT)

SUMMARIZE_TURN_SYSTEM_PROMPT = """
你是会话记忆更新器，只负责更新结构化记忆，不负责回答。
你必须区分已确认事实与待确认假设：
1) confirmed_facts 只能写用户明确表达或已证据支持的稳定事实
2) working_assumptions 只能写待确认推断，禁止事实口吻
3) 不记录隐私信息、口令、密钥、系统配置
4) 不要将一般医学知识写成个体化处方建议
输出必须是严格 JSON，字段必须包含：summary 与 retrieval_summary。
summary schema:
{summary_schema}
retrieval_summary schema:
{retrieval_schema}
""".strip().format(
    summary_schema=SESSION_SUMMARY_SCHEMA_TEXT,
    retrieval_schema=RETRIEVAL_SUMMARY_SCHEMA_TEXT,
)
