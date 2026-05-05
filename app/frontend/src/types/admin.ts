import type { ChatRole, SessionRecord } from "./chat";
import type { UnknownRecord } from "./shared";

// 管理后台历史类型定义，覆盖 readiness、会话、Agent 观测等响应结构。
export interface ReadinessReport {
  level?: "ready" | "degraded" | "unavailable";
  checked_at?: string;
  critical_not_ready?: string[];
  optional_issues?: string[];
}

export interface AdminStats {
  total_sessions: number;
  total_messages: number;
  today_sessions: number;
  today_messages: number;
}

export interface AdminSessionListResponse {
  data: SessionRecord[];
  total: number;
}

export interface AdminSessionMessage {
  id?: number;
  role: ChatRole | string;
  content: string;
  created_at?: string;
}

export interface AgentQueryRecord extends UnknownRecord {
  query?: string;
}

export interface AgentToolObservation extends UnknownRecord {
  agent?: string;
  tool_name?: string;
  requested_tool_name?: string;
  action?: string;
  subquery?: string;
  question?: string;
  query_records?: AgentQueryRecord[];
}

export interface AgentMemoryBefore extends UnknownRecord {
  qa_memory_text?: string;
  retrieval_question_index_text?: string;
}

export interface AgentMemoryCompressionStage extends UnknownRecord {
  stage_name?: string;
  content_lines?: string[];
}

export interface AgentOrchestratorDecision extends UnknownRecord {
  prompt_budget_limit?: number | string;
  compression_count?: number;
  prompt_token_count_before_last_decision?: number;
  prompt_token_count_after_last_compression?: number;
  aborted_for_prompt_budget?: boolean;
}

export interface AgentObservabilityFlowEventDetail extends UnknownRecord {
  tool_call_count?: number;
  model_input_prompt?: string;
  model_output_text?: string;
  tool_call_inputs?: string;
  tool_call_results?: string;
}

export interface AgentObservabilityFlowEvent extends UnknownRecord {
  event_id?: string;
  event_time?: string;
  lane?: string;
  stage?: string;
  title?: string;
  summary?: string;
  actor?: string;
  detail?: AgentObservabilityFlowEventDetail;
}

export interface SessionObservabilityTurn extends UnknownRecord {
  turn_id: string;
  status?: string;
  question?: string;
  answer_digest?: string;
  total_tokens?: number;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  memory_before?: AgentMemoryBefore;
  memory_compression?: AgentMemoryCompressionStage[];
  tool_observations?: AgentToolObservation[];
  orchestrator_decision?: AgentOrchestratorDecision;
  flow_events?: AgentObservabilityFlowEvent[];
}

export interface AdminSessionMessagesResponse {
  session: SessionRecord & UnknownRecord;
  messages: AdminSessionMessage[];
}

export interface AdminSessionObservabilityResponse {
  turns: SessionObservabilityTurn[];
}

export interface AgentObservabilitySummary extends UnknownRecord {
  total_runs: number;
  avg_duration_ms: number;
  total_tokens: number;
  total_llm_calls: number;
  total_tool_calls?: number;
  total_attempted_tool_actions?: number;
  total_blocked_tool_actions?: number;
  avg_tool_success_rate?: number;
  avg_execution_tool_success_rate?: number;
  avg_attempted_action_success_rate?: number;
  strategy_error_types?: Record<string, number>;
  execution_error_types?: Record<string, number>;
}

export interface AgentObservabilityRun extends UnknownRecord {
  session_id?: string;
  timestamp?: string;
  llm_calls?: number;
  attempted_tool_actions?: number;
  blocked_tool_actions?: number;
  tool_calls?: number;
  total_tokens?: number;
}

export interface AgentObservabilityTurn extends AgentObservabilityRun, SessionObservabilityTurn {}
