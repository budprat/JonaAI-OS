/**
 * LoginPage component spec (Supabase Auth edition).
 *
 * Validates the new Supabase-backed login + sign-up flow:
 * - Mode toggle (sign-in / sign-up)
 * - Email/password form submission
 * - Error rendering from Supabase responses
 * - Success info message after sign-up email confirmation
 * - Loading state
 *
 * Spies are installed on supabase.auth methods via installMethodSpy
 * (from spec-helpers.tsx) to keep tests fully offline. The supabase
 * client is imported from a placeholder URL in tests, so no network
 * calls actually occur.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LoginPage } from "../LoginPage";
import { supabase } from "@/lib/supabase";
import { PROXY_AUTH_SESSION_STORAGE_KEY } from "../proxySession";
import {
  installFetchSpy,
  installMethodSpy,
  jsonOk,
  rejectWith,
  resetSpies,
  resolveWith,
  returnWith,
} from "@/lib/spec-helpers";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("LoginPage (Supabase Auth)", () => {
  let signInSpy: ReturnType<typeof installMethodSpy>;
  let signUpSpy: ReturnType<typeof installMethodSpy>;
  let setSessionSpy: ReturnType<typeof installMethodSpy>;

  beforeEach(() => {
    window.localStorage.clear();
    resetSpies();
    signInSpy = installMethodSpy(supabase.auth, "signInWithPassword");
    signUpSpy = installMethodSpy(supabase.auth, "signUp");
    setSessionSpy = installMethodSpy(supabase.auth, "setSession");
  });

  // ---- Rendering ----

  it("renders email + password form with sign-in as default mode", () => {
    render(<LoginPage />);

    expect(screen.getByTestId("form-login")).toBeInTheDocument();
    expect(screen.getByTestId("input-email")).toBeInTheDocument();
    expect(screen.getByTestId("input-password")).toBeInTheDocument();
    expect(screen.getByTestId("button-login")).toHaveTextContent("Sign In");
  });

  it("renders branding (logo, title, tagline)", () => {
    render(<LoginPage />);

    expect(screen.getByTestId("text-login-title")).toHaveTextContent("Jona AI");
    expect(screen.getByText("Chat with the Planet")).toBeInTheDocument();
    expect(screen.getByTestId("img-login-logo")).toBeInTheDocument();
  });

  it("renders sign-in/sign-up mode toggle", () => {
    render(<LoginPage />);
    expect(screen.getByTestId("button-mode-signin")).toBeInTheDocument();
    expect(screen.getByTestId("button-mode-signup")).toBeInTheDocument();
  });

  it("renders the main webpage external link", () => {
    render(<LoginPage />);
    const link = screen.getByTestId("link-main-webpage");
    expect(link).toHaveAttribute("href", "https://site.jonaai.com");
    expect(link).toHaveAttribute("target", "_blank");
  });

  // ---- Sign-in flow ----

  it("calls supabase signInWithPassword with email + password", async () => {
    resolveWith(signInSpy, {
      data: { user: { id: "u1" }, session: { access_token: "tok" } },
      error: null,
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "test@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "secret123" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(signInSpy).toHaveBeenCalledWith({
        email: "test@example.com",
        password: "secret123",
      });
    });
  });

  it("shows error message when sign-in fails", async () => {
    resolveWith(signInSpy, {
      data: { user: null, session: null },
      error: { message: "Invalid login credentials" },
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "bad@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "wrong" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(screen.getByTestId("text-login-error")).toHaveTextContent("Invalid login credentials");
    });
  });

  it("falls back to local auth proxy when direct sign-in returns a fetch error", async () => {
    resolveWith(signInSpy, {
      data: { user: null, session: null },
      error: { message: "Failed to fetch", name: "AuthRetryableFetchError", status: 0 },
    });
    const fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, {
      ok: false,
      status: 400,
      json: async () => ({ msg: "Invalid login credentials" }),
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "missing@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "wrong-password" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/supabase-auth/signin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "missing@example.com", password: "wrong-password" }),
      });
      expect(screen.getByTestId("text-login-error")).toHaveTextContent("Invalid login credentials");
    });
  });

  it("stores proxied sign-in session when Supabase setSession cannot fetch user", async () => {
    resolveWith(signInSpy, {
      data: { user: null, session: null },
      error: { message: "Failed to fetch", name: "AuthRetryableFetchError", status: 0 },
    });
    const fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, {
      ok: true,
      json: async () => ({
        access_token: "proxy-access-token",
        refresh_token: "proxy-refresh-token",
        expires_in: 3600,
        token_type: "bearer",
        user: {
          id: "u-proxy",
          aud: "authenticated",
          role: "authenticated",
          email: "proxy@example.com",
          app_metadata: {},
          user_metadata: {},
          created_at: "2026-01-01T00:00:00Z",
        },
      }),
    });
    resolveWith(setSessionSpy, {
      data: { user: null, session: null },
      error: { message: "Failed to fetch", name: "AuthRetryableFetchError", status: 0 },
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "proxy@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "valid-password" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      const rawSession = window.localStorage.getItem(PROXY_AUTH_SESSION_STORAGE_KEY);
      expect(rawSession).toContain("proxy-access-token");
      expect(rawSession).toContain("proxy@example.com");
      expect(setSessionSpy).toHaveBeenCalledWith({
        access_token: "proxy-access-token",
        refresh_token: "proxy-refresh-token",
      });
    });
  });

  // ---- Sign-up flow ----

  it("switches to sign-up mode when toggle clicked", () => {
    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));

    expect(screen.getByTestId("button-login")).toHaveTextContent("Create Account");
  });

  it("calls supabase signUp in sign-up mode", async () => {
    resolveWith(signUpSpy, {
      data: { user: { id: "u2" }, session: null },
      error: null,
    });

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "newpass123" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(signUpSpy).toHaveBeenCalledWith({
        email: "new@example.com",
        password: "newpass123",
      });
    });
  });

  it("shows email confirmation message after successful sign-up without session", async () => {
    resolveWith(signUpSpy, {
      data: { user: { id: "u3" }, session: null },
      error: null,
    });

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "verify@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "longenough" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(screen.getByTestId("text-login-info")).toHaveTextContent(/confirmation link/i);
    });
  });

  it("falls back to local auth proxy when direct sign-up returns a fetch error", async () => {
    resolveWith(signUpSpy, {
      data: { user: null, session: null },
      error: { message: "Failed to fetch", name: "AuthRetryableFetchError", status: 0 },
    });
    const fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, jsonOk({ id: "u-proxy", email: "proxy@example.com" }));

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "proxy@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "longenough" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/supabase-auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "proxy@example.com", password: "longenough" }),
      });
      expect(screen.getByTestId("text-login-info")).toHaveTextContent(/confirmation link/i);
    });
  });

  it("falls back to local auth proxy when direct sign-up throws", async () => {
    rejectWith(signUpSpy, new TypeError("Failed to fetch"));
    const fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, jsonOk({ id: "u-proxy-throw", email: "proxy-throw@example.com" }));

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "proxy-throw@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "longenough" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/supabase-auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "proxy-throw@example.com", password: "longenough" }),
      });
      expect(screen.getByTestId("text-login-info")).toHaveTextContent(/confirmation link/i);
    });
  });

  it("shows a helpful message when Supabase rate-limits sign-up emails", async () => {
    resolveWith(signUpSpy, {
      data: { user: null, session: null },
      error: { message: "Failed to fetch", name: "AuthRetryableFetchError", status: 0 },
    });
    const fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, {
      ok: false,
      status: 429,
      json: async () => ({
        code: 429,
        error_code: "over_email_send_rate_limit",
        msg: "email rate limit exceeded",
      }),
    });

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "rate-limited@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "longenough" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/supabase-auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "rate-limited@example.com", password: "longenough" }),
      });
      expect(screen.getByTestId("text-login-error")).toHaveTextContent(
        "Too many sign-up emails were requested.",
      );
    });
  });

  it("shows a helpful message when the auth proxy cannot reach Supabase", async () => {
    resolveWith(signUpSpy, {
      data: { user: null, session: null },
      error: { message: "Failed to fetch", name: "AuthRetryableFetchError", status: 0 },
    });
    const fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, {
      ok: false,
      status: 502,
      json: async () => ({
        error_code: "supabase_auth_unreachable",
        msg: "Could not reach Supabase Auth. Check network connectivity and try again.",
      }),
    });

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "network@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "longenough" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/supabase-auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "network@example.com", password: "longenough" }),
      });
      expect(screen.getByTestId("text-login-error")).toHaveTextContent(
        "Could not reach Supabase Auth.",
      );
    });
  });

  it("shows error message when sign-up fails", async () => {
    resolveWith(signUpSpy, {
      data: { user: null, session: null },
      error: { message: "Password is too short" },
    });

    render(<LoginPage />);

    fireEvent.click(screen.getByTestId("button-mode-signup"));
    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "x@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "shortx" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(screen.getByTestId("text-login-error")).toHaveTextContent("Password is too short");
    });
  });

  // ---- Loading state ----

  it("disables submit button while sign-in is pending", async () => {
    let resolveSignIn!: (value: unknown) => void;
    const pending = new Promise((resolve) => {
      resolveSignIn = resolve;
    });
    returnWith(signInSpy, pending);

    render(<LoginPage />);

    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "u@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "pass" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(screen.getByTestId("button-login")).toBeDisabled();
    });

    resolveSignIn({ data: { user: null, session: null }, error: null });

    await waitFor(() => {
      expect(screen.getByTestId("button-login")).not.toBeDisabled();
    });
  });

  // ---- Form validation ----

  it("renders email and password as required", () => {
    render(<LoginPage />);

    expect(screen.getByTestId("input-email")).toBeRequired();
    expect(screen.getByTestId("input-password")).toBeRequired();
  });

  it("does not show error or info message initially", () => {
    render(<LoginPage />);

    expect(screen.queryByTestId("text-login-error")).not.toBeInTheDocument();
    expect(screen.queryByTestId("text-login-info")).not.toBeInTheDocument();
  });

  it("clears error when switching auth mode", async () => {
    resolveWith(signInSpy, {
      data: { user: null, session: null },
      error: { message: "Bad credentials" },
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByTestId("input-email"), {
      target: { value: "u@example.com" },
    });
    fireEvent.change(screen.getByTestId("input-password"), {
      target: { value: "p" },
    });
    fireEvent.submit(screen.getByTestId("form-login"));

    await waitFor(() => {
      expect(screen.getByTestId("text-login-error")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("button-mode-signup"));

    expect(screen.queryByTestId("text-login-error")).not.toBeInTheDocument();
  });
});
