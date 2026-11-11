// ABOUTME: useAuth hook — accesses Supabase auth state from AuthContext.
// Split from AuthProvider.tsx so AuthProvider.tsx only exports the component
// (required by react-refresh/only-export-components lint rule).

import { useContext } from "react";
import { AuthContext, type AuthContextValue } from "./auth-context";

/**
 * Hook to access the current Supabase auth state.
 *
 * Usage:
 *   const { user, accessToken, signOut, loading } = useAuth();
 *   if (loading) return <Spinner />;
 *   if (!user) return <LoginPage />;
 */
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value.accessToken || typeof window === "undefined") {
    return value;
  }

  // Backward-compatible bridge for isolated component tests and older pages
  // rendered without AuthProvider. Application runtime should get tokens from
  // Supabase AuthProvider, not this legacy key.
  try {
    const raw = window.localStorage.getItem("jonaai-token");
    const legacyToken = raw ? JSON.parse(raw) : null;
    if (legacyToken) {
      return { ...value, accessToken: legacyToken, loading: false };
    }
  } catch {
    // Ignore malformed legacy storage.
  }
  return value;
}
