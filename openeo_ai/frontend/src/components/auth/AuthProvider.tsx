// ABOUTME: React context provider exposing Supabase auth state to the app.
// Wraps the app, listens to onAuthStateChange, provides session/user/loading/signOut.

import { useEffect, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";
import { AuthContext, type AuthContextValue } from "./auth-context";
import {
  clearProxyAuthSession,
  PROXY_AUTH_SESSION_EVENT,
  PROXY_AUTH_SESSION_STORAGE_KEY,
  readProxyAuthSession,
} from "./proxySession";

/** Path that password-recovery emails link back to. */
export const PASSWORD_RESET_PATH = "/reset-password";

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  // Seeded from the URL: the client exchanges the recovery code during init, so
  // PASSWORD_RECOVERY can fire before the listener below is attached.
  const [passwordRecovery, setPasswordRecovery] = useState(
    () => window.location.pathname === PASSWORD_RESET_PATH,
  );

  useEffect(() => {
    // Restore session from localStorage on first mount
    let mounted = true;
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!mounted) return;
        setSession(data.session ?? readProxyAuthSession());
      })
      .catch(() => {
        if (!mounted) return;
        setSession(readProxyAuthSession());
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    // Listen for sign-in / sign-out / token-refresh events
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, newSession) => {
      if (event === "PASSWORD_RECOVERY") setPasswordRecovery(true);
      setSession(newSession ?? readProxyAuthSession());
    });

    // The fallback session changed. A live Supabase session still takes precedence:
    // LoginPage clears the fallback right after a normal sign-in, and that must not
    // sign the user straight back out.
    const handleProxySessionChange = () => {
      supabase.auth
        .getSession()
        .then(({ data }) => {
          if (mounted) setSession(data.session ?? readProxyAuthSession());
        })
        .catch(() => {
          if (mounted) setSession(readProxyAuthSession());
        });
    };
    const handleStorageChange = (event: StorageEvent) => {
      if (event.key === PROXY_AUTH_SESSION_STORAGE_KEY) handleProxySessionChange();
    };

    window.addEventListener(PROXY_AUTH_SESSION_EVENT, handleProxySessionChange);
    window.addEventListener("storage", handleStorageChange);

    return () => {
      mounted = false;
      window.removeEventListener(PROXY_AUTH_SESSION_EVENT, handleProxySessionChange);
      window.removeEventListener("storage", handleStorageChange);
      subscription.unsubscribe();
    };
  }, []);

  // A fallback session has no refresh, so sign it out when it expires.
  useEffect(() => {
    const expiresAt = session?.expires_at;
    if (!session || typeof expiresAt !== "number") return;
    if (readProxyAuthSession()?.access_token !== session.access_token) return;
    // setTimeout overflows past about 24.8 days and would fire at once, over and over.
    const delay = Math.min(Math.max(0, expiresAt * 1000 - Date.now()) + 1000, 2 ** 31 - 1);
    const timer = window.setTimeout(
      () => setSession((current) => (current === session ? readProxyAuthSession() : current)),
      delay,
    );
    return () => window.clearTimeout(timer);
  }, [session]);

  const signOut = async () => {
    clearProxyAuthSession();
    setSession(null);
    await supabase.auth.signOut().catch(() => {});
  };

  const value: AuthContextValue = {
    session,
    user: session?.user ?? null,
    accessToken: session?.access_token ?? null,
    loading,
    signOut,
    passwordRecovery,
    clearPasswordRecovery: () => setPasswordRecovery(false),
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
