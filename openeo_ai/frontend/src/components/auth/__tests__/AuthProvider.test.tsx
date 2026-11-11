/**
 * AuthProvider session source: a live Supabase session outranks the fallback session, so
 * clearing the fallback after a normal sign-in doesn't sign the user back out.
 */
import { describe, it, expect, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { supabase } from "@/lib/supabase";
import { AuthProvider } from "../AuthProvider";
import { clearProxyAuthSession, writeProxyAuthSession } from "../proxySession";
import { useAuth } from "../useAuth";
import { installMethodSpy, resolveWith } from "@/lib/spec-helpers";
import type { Session } from "@supabase/supabase-js";

const originalGetSession = supabase.auth.getSession;
const originalOnAuthStateChange = supabase.auth.onAuthStateChange;

/** Supabase reports `current` as its session, and stays quiet otherwise. */
function supabaseHolds(current: Session | null) {
  resolveWith(installMethodSpy(supabase.auth, "getSession"), { data: { session: current } });
  installMethodSpy(supabase.auth, "onAuthStateChange").mockReturnValue({
    data: { subscription: { unsubscribe: () => {} } },
  });
}

function session(token: string, expiresInSeconds = 3600): Session {
  return {
    access_token: token,
    refresh_token: "r",
    expires_in: expiresInSeconds,
    expires_at: Math.floor(Date.now() / 1000) + expiresInSeconds,
    token_type: "bearer",
    user: { id: "u1", email: "a@b.co" },
  } as unknown as Session;
}

function TokenProbe() {
  const { accessToken } = useAuth();
  return <div data-testid="token">{accessToken ?? "none"}</div>;
}

afterEach(() => {
  supabase.auth.getSession = originalGetSession;
  supabase.auth.onAuthStateChange = originalOnAuthStateChange;
  localStorage.clear();
});

describe("AuthProvider session source", () => {
  it("keeps a Supabase session when the fallback session is cleared", async () => {
    supabaseHolds(session("live"));
    render(
      <AuthProvider>
        <TokenProbe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("token")).toHaveTextContent("live"));

    // LoginPage does this right after a successful direct sign-in.
    await act(async () => {
      clearProxyAuthSession();
    });

    expect(screen.getByTestId("token")).toHaveTextContent("live");
  });

  it("uses the fallback session when Supabase has none", async () => {
    supabaseHolds(null);
    render(
      <AuthProvider>
        <TokenProbe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("token")).toHaveTextContent("none"));

    await act(async () => {
      writeProxyAuthSession(session("fallback"));
    });

    await waitFor(() => expect(screen.getByTestId("token")).toHaveTextContent("fallback"));
  });

  it("doesn't loop on a fallback session that expires months away", async () => {
    supabaseHolds(null);
    writeProxyAuthSession(session("far", 90 * 24 * 3600));
    let renders = 0;
    function CountingProbe() {
      renders += 1;
      return <TokenProbe />;
    }
    render(
      <AuthProvider>
        <CountingProbe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("token")).toHaveTextContent("far"));
    const settled = renders;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(renders).toBe(settled);
  });
});
