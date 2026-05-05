import { create } from "zustand";

import { hasAdminToken } from "../../../shared/api/authClient";

interface AuthState {
  isAdminAuthenticated: boolean;
  isLoginDialogOpen: boolean;
  setAuthenticated: (value: boolean) => void;
  openLoginDialog: () => void;
  closeLoginDialog: () => void;
}

// 管理员认证 UI 状态，初始值从本地 token 是否存在推断。
export const useAuthStore = create<AuthState>((set) => ({
  isAdminAuthenticated: hasAdminToken(),
  isLoginDialogOpen: false,
  setAuthenticated: (value) => set({ isAdminAuthenticated: value }),
  openLoginDialog: () => set({ isLoginDialogOpen: true }),
  closeLoginDialog: () => set({ isLoginDialogOpen: false }),
}));
