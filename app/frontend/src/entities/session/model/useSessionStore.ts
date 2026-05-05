import { create } from "zustand";

const USER_ID_KEY = "hypertensionkg_user_id";
const CURRENT_SESSION_KEY_PREFIX = "current_session_";

// UI 层消息模型同时承载后端消息 ID、前端临时 ID、思考内容和附件预览。
export interface UiMessage {
  id?: number;
  clientId?: string;
  role: "user" | "assistant";
  content: string;
  thinkContent?: string;
  image?: string | null;
  createdAt?: string;
}

function getOrCreateUserId(): string {
  // 本地匿名用户 ID 用于把普通会话和浏览器实例绑定。
  const cached = window.localStorage.getItem(USER_ID_KEY);
  if (cached) return cached;
  const next = `user_${Math.random().toString(36).slice(2, 10)}`;
  window.localStorage.setItem(USER_ID_KEY, next);
  return next;
}

function getStoredSessionId(userId: string): string | null {
  return window.localStorage.getItem(`${CURRENT_SESSION_KEY_PREFIX}${userId}`) || null;
}

function persistSessionId(userId: string, sessionId: string | null): void {
  const key = `${CURRENT_SESSION_KEY_PREFIX}${userId}`;
  if (sessionId) {
    window.localStorage.setItem(key, sessionId);
    return;
  }
  window.localStorage.removeItem(key);
}

const initialUserId = getOrCreateUserId();

interface SessionState {
  userId: string;
  currentSessionId: string | null;
  messages: UiMessage[];
  draft: string;
  copiedMessageKey: string;
  loading: boolean;
  selectedFile: File | null;
  filePreview: string | null;
  currentJobId: string;
  stoppingJob: boolean;
  setCurrentSessionId: (sessionId: string | null) => void;
  setMessages: (messages: UiMessage[]) => void;
  updateMessages: (updater: (messages: UiMessage[]) => UiMessage[]) => void;
  appendMessages: (...messages: UiMessage[]) => void;
  setDraft: (draft: string) => void;
  setCopiedMessageKey: (key: string) => void;
  setSelectedFile: (file: File | null) => void;
  setFilePreview: (preview: string | null) => void;
  clearAttachment: () => void;
  setLoading: (loading: boolean) => void;
  setCurrentJobId: (jobId: string) => void;
  setStoppingJob: (stopping: boolean) => void;
  resetComposer: () => void;
  clearSessionSelection: () => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  // Zustand store 管理聊天页跨组件共享的会话、输入框、附件和任务状态。
  userId: initialUserId,
  currentSessionId: getStoredSessionId(initialUserId),
  messages: [],
  draft: "",
  copiedMessageKey: "",
  loading: false,
  selectedFile: null,
  filePreview: null,
  currentJobId: "",
  stoppingJob: false,
  setCurrentSessionId: (sessionId) => {
    persistSessionId(get().userId, sessionId);
    set({ currentSessionId: sessionId });
  },
  setMessages: (messages) => set({ messages }),
  updateMessages: (updater) => set((state) => ({ messages: updater(state.messages) })),
  appendMessages: (...messages) => set((state) => ({ messages: [...state.messages, ...messages] })),
  setDraft: (draft) => set({ draft }),
  setCopiedMessageKey: (key) => set({ copiedMessageKey: key }),
  setSelectedFile: (selectedFile) => set({ selectedFile }),
  setFilePreview: (filePreview) => set({ filePreview }),
  clearAttachment: () => set({ selectedFile: null, filePreview: null }),
  setLoading: (loading) => set({ loading }),
  setCurrentJobId: (currentJobId) => set({ currentJobId }),
  setStoppingJob: (stoppingJob) => set({ stoppingJob }),
  resetComposer: () => set({ draft: "", selectedFile: null, filePreview: null }),
  clearSessionSelection: () => {
    persistSessionId(get().userId, null);
    set({ currentSessionId: null, messages: [] });
  },
}));
