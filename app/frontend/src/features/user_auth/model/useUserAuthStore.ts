import { create } from "zustand";

import { hasUserToken, type AuthenticatedUser } from "../../../shared/api/userAuthClient";

interface UserAuthState {
  user: AuthenticatedUser | null;
  isAuthenticated: boolean;
  setUser: (user: AuthenticatedUser) => void;
  clearUser: () => void;
}

export const useUserAuthStore = create<UserAuthState>((set) => ({
  user: null,
  isAuthenticated: hasUserToken(),
  setUser: (user) => set({ user, isAuthenticated: true }),
  clearUser: () => set({ user: null, isAuthenticated: false }),
}));
