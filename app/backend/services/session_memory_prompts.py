"""会话记忆 Resolver/Summarizer Prompt 常量。"""

RESOLVED_QUERY_SCHEMA_TEXT = """
{
  "standalone_question": "string",
  "intent": "string",
  "question_mode": "repeat_medical_question|memory_lookup|capability_query|direct_query|medical_new_question|medical_gap_followup",
  "subqueries": ["string"],
  "must_cover": ["string"],
  "entities": ["string"],
  "filters": ["string"],
  "preferred_sources": ["memory|kg|rag|direct|capability"],
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

TURN_MEMORY_PROJECTION_SCHEMA_TEXT = """
{
  "answered_points": ["string"],
  "evidence_gaps": ["string"],
  "focus_entities": ["string"],
  "covered_topics": ["string"],
  "risk_notes": ["string"],
  "assistant_reply_digest": "string",
  "question_scope": "medical_qa|capability_query|administrative|small_talk|unknown"
}
""".strip()

RESOLVE_QUERY_SYSTEM_PROMPT = """
你是会话问题改写器，只负责把当前问题补全为独立问题，不负责回答。
你必须只基于提供的最近对话摘要 recent_messages_digest 与会话记忆，禁止编造患者条件。
你必须先判断这题是否可以仅凭当前会话记忆回答：
1) 用户身份/历史自述/重复确认/“你还记得吗”类问题优先输出 question_mode=memory_lookup 或 repeat_medical_question，preferred_sources 优先 memory
2) “你能做什么/你的功能/你是谁”类问题输出 question_mode=capability_query，preferred_sources 优先 capability
3) 轻量通用问题可输出 question_mode=direct_query，preferred_sources 优先 direct
4) 只有真正需要医学知识检索的新问题，才输出 question_mode=medical_new_question 或 medical_gap_followup，并使用 kg/rag
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
5) 对语义重复条目要合并覆盖，不得无限追加
6) summary 与 retrieval_summary 的各数组字段最多保留 5 条，优先保留仍影响后续回答/检索的条目
7) 只会提供 turn_memory_projection（而非原始检索全文），不得要求原始工具结果
8) answered_points 只能保留用户真正得到的结论或证据支持点，禁止写执行日志、节点状态、门控结果、dispatch/events 等流程语句
输出必须是严格 JSON，字段必须包含：summary 与 retrieval_summary。
summary schema:
{summary_schema}
retrieval_summary schema:
{retrieval_schema}
turn_memory_projection schema:
{turn_memory_projection_schema}
""".strip().format(
    summary_schema=SESSION_SUMMARY_SCHEMA_TEXT,
    retrieval_schema=RETRIEVAL_SUMMARY_SCHEMA_TEXT,
  turn_memory_projection_schema=TURN_MEMORY_PROJECTION_SCHEMA_TEXT,
)
