// ABOUTME: Local fallback session bridge for Supabase auth proxied through FastAPI.
// Used when the browser cannot fetch Supabase Auth directly but the backend proxy can.

import type { Session } from "@supabase/supabase-js";

export const PROXY_AUTH_SESSION_EVENT = "jonaai-proxy-auth-session";
export const PROXY_AUTH_SESSION_STORAGE_KEY = "jonaai-proxy-auth-session";

export interface ProxySessionPayload {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  expires_at?: number;
  token_type?: string;
  user?: unknown;
}

export function isNetworkAuthError(error: unknown): boolean {
  if (!error) return false;

  const record = isRecord(error) ? error : {};
  const message =
    typeof record.message === "string" ? record.message : error instanceof Error ? error.message : "";
  const name = typeof record.name === "string" ? record.name : "";
  const status = typeof record.status === "number" ? record.status : undefined;

  return (
    name === "AuthRetryableFetchError" ||
    status === 0 ||
    message.includes("Failed to fetch") ||
    message.includes("NetworkError")
  );
}

export function buildProxyAuthSession(payload: ProxySessionPayload): Session | null {
  if (
    typeof payload.access_token !== "string" ||
    typeof payload.refresh_token !== "string" ||
    !isRecord(payload.user)
  ) {
    return null;
  }

  const expiresIn =
    typeof payload.expires_in === "number" && payload.expires_in > 0 ? payload.expires_in : 3600;
  const expiresAt =
    typeof payload.expires_at === "number" && payload.expires_at > 0
      ? payload.expires_at
      : Math.floor(Date.now() / 1000) + expiresIn;

  return {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token,
    expires_in: expiresIn,
    expires_at: expiresAt,
    token_type: "bearer",
    user: payload.user as unknown as Session["user"],
  };
}

export function readProxyAuthSession(): Session | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(PROXY_AUTH_SESSION_STORAGE_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw) as unknown;
    // Nothing refreshes this fallback session, so once it expires the user signs in again
    // rather than staying in an app whose every request is refused.
    if (isValidStoredSession(parsed) && !isExpired(parsed)) return parsed;
  } catch {
    // Clear malformed fallback state below.
  }

  clearProxyAuthSession();
  return null;
}

export function writeProxyAuthSession(session: Session): void {
  if (typeof window === "undefined") return;

  window.localStorage.setItem(PROXY_AUTH_SESSION_STORAGE_KEY, JSON.stringify(session));
  window.dispatchEvent(new CustomEvent(PROXY_AUTH_SESSION_EVENT, { detail: session }));
}

export function clearProxyAuthSession(): void {
  if (typeof window === "undefined") return;

  window.localStorage.removeItem(PROXY_AUTH_SESSION_STORAGE_KEY);
  window.dispatchEvent(new CustomEvent(PROXY_AUTH_SESSION_EVENT, { detail: null }));
}

function isValidStoredSession(value: unknown): value is Session {
  return (
    isRecord(value) &&
    typeof value.access_token === "string" &&
    typeof value.refresh_token === "string" &&
    isRecord(value.user)
  );
}

function isExpired(session: Session): boolean {
  return typeof session.expires_at === "number" && session.expires_at * 1000 <= Date.now();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
