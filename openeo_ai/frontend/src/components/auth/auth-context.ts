// ABOUTME: Shared React context for Supabase auth state.
// Split out from AuthProvider.tsx to satisfy react-refresh/only-export-components rule.

import { createContext } from "react";
import type { Session, User } from "@supabase/supabase-js";

export interface AuthContextValue {
  session: Session | null;
  user: User | null;
  /** Supabase JWT access_token, used as Bearer for backend API + WebSocket auth */
  accessToken: string | null;
  /** True while restoring the session from storage on first mount */
  loading: boolean;
  signOut: () => Promise<void>;
  /** True while a password-recovery link is being handled; holds the user on the reset page */
  passwordRecovery: boolean;
  clearPasswordRecovery: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  session: null,
  user: null,
  accessToken: null,
  loading: true,
  signOut: async () => {},
  passwordRecovery: false,
  clearPasswordRecovery: () => {},
});
