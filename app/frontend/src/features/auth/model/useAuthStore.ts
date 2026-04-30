import { create } from "zustand";

import { hasAdminToken } from "../../../shared/api/authClient";

interface AuthState {
  isAdminAuthenticated: boolean;
  isLoginDialogOpen: boolean;
  setAuthenticated: (value: boolean) => void;
  openLoginDialog: () => void;
  closeLoginDialog: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  isAdminAuthenticated: hasAdminToken(),
  isLoginDialogOpen: false,
  setAuthenticated: (value) => set({ isAdminAuthenticated: value }),
  openLoginDialog: () => set({ isLoginDialogOpen: true }),
  closeLoginDialog: () => set({ isLoginDialogOpen: false }),
}));
