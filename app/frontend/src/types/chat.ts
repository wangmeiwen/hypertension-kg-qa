// 聊天域历史类型定义，描述会话、消息、上传结果和流式事件。
export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id?: number;
  clientId?: string;
  role: ChatRole;
  content: string;
  thinkContent?: string;
  image?: string | null;
  created_at?: string;
}

export interface SessionRecord {
  id: string;
  user_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface PersistedSessionMessage {
  id: number;
  role: ChatRole;
  content: string;
  think_content?: string;
  created_at?: string;
}

export interface SessionDetail extends SessionRecord {
  messages: PersistedSessionMessage[];
}

export interface UploadChatFileResult {
  is_image?: boolean;
  base64?: string;
  file_id?: string;
  aliyun_file_id?: string;
  original_name?: string;
  [key: string]: unknown;
}

export interface RollbackQuestionResult {
  question?: string;
  [key: string]: unknown;
}

export type StreamProcessEvent =
  | { type: "job"; job_id?: string }
  | { type: "session"; session_id?: string; user_message_id?: number }
  | { type: "persisted"; session_id?: string; user_message_id?: number; assistant_message_id?: number }
  | { type: "msg"; msg?: string; stage?: string }
  | { type: "reply"; msg?: string }
  | { type: "done"; msg?: string; status?: string };

export type ThinkStatus = string;
