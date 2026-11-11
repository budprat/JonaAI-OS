import { describe, it, expect, beforeEach } from "vitest";
import { PROXY_AUTH_SESSION_STORAGE_KEY, readProxyAuthSession } from "../proxySession";

function store(expiresAt: number) {
  window.localStorage.setItem(
    PROXY_AUTH_SESSION_STORAGE_KEY,
    JSON.stringify({ access_token: "a", refresh_token: "r", expires_at: expiresAt, user: { id: "u" } }),
  );
}

describe("proxy fallback session", () => {
  beforeEach(() => window.localStorage.clear());

  it("is used while it is valid", () => {
    store(Math.floor(Date.now() / 1000) + 600);
    expect(readProxyAuthSession()?.access_token).toBe("a");
  });

  it("counts as signed out once expired, and is cleared", () => {
    store(Math.floor(Date.now() / 1000) - 1);
    expect(readProxyAuthSession()).toBeNull();
    expect(window.localStorage.getItem(PROXY_AUTH_SESSION_STORAGE_KEY)).toBeNull();
  });
});
