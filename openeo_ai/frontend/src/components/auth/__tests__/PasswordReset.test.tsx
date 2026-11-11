/**
 * Forgot-password flow: requesting a link from LoginPage, choosing a new
 * password on ResetPasswordPage, and AuthProvider holding recovery users on
 * that page. Supabase methods are spied so the tests stay offline.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { supabase } from "@/lib/supabase";
import { AuthContext, type AuthContextValue } from "../auth-context";
import { AuthProvider } from "../AuthProvider";
import { LoginPage } from "../LoginPage";
import { ResetPasswordPage } from "../ResetPasswordPage";
import { useAuth } from "../useAuth";
import { installMethodSpy, resetSpies, resolveWith } from "@/lib/spec-helpers";

const fakeSession = { access_token: "t", user: { email: "a@b.co" } } as AuthContextValue["session"];

function renderResetPage(session: AuthContextValue["session"], onDone = vi.fn()) {
  const value: AuthContextValue = {
    session,
    user: null,
    accessToken: null,
    loading: false,
    signOut: async () => {},
    passwordRecovery: true,
    clearPasswordRecovery: () => {},
  };
  render(
    <AuthContext.Provider value={value}>
      <ResetPasswordPage onDone={onDone} />
    </AuthContext.Provider>,
  );
  return onDone;
}

function fillPasswords(password: string, confirm: string) {
  fireEvent.change(screen.getByTestId("input-new-password"), { target: { value: password } });
  fireEvent.change(screen.getByTestId("input-confirm-password"), { target: { value: confirm } });
  fireEvent.submit(screen.getByTestId("form-reset-password"));
}

describe("forgot password request", () => {
  let resetSpy: ReturnType<typeof installMethodSpy>;

  beforeEach(() => {
    resetSpies();
    resetSpy = installMethodSpy(supabase.auth, "resetPasswordForEmail");
    render(<LoginPage />);
    fireEvent.click(screen.getByTestId("button-forgot-toggle"));
  });

  function submit(email: string) {
    fireEvent.change(screen.getByTestId("input-email"), { target: { value: email } });
    fireEvent.submit(screen.getByTestId("form-login"));
  }

  it("asks only for an email", () => {
    expect(screen.queryByTestId("input-password")).not.toBeInTheDocument();
    expect(screen.getByTestId("button-login")).toHaveTextContent("Send Reset Link");
  });

  it("requests a link that returns to the reset page, without confirming the account exists", async () => {
    resolveWith(resetSpy, { data: {}, error: null });
    submit("a@b.co");

    await waitFor(() => expect(screen.getByTestId("text-login-info")).toBeInTheDocument());
    expect(resetSpy).toHaveBeenCalledWith("a@b.co", {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    expect(screen.getByTestId("text-login-info")).toHaveTextContent("If an account exists");
  });

  it("explains rate limiting", async () => {
    resolveWith(resetSpy, {
      data: null,
      error: { message: "rate", status: 429, code: "over_email_send_rate_limit" },
    });
    submit("a@b.co");

    await waitFor(() =>
      expect(screen.getByTestId("text-login-error")).toHaveTextContent("Too many reset emails"),
    );
  });

  it("returns to sign in", () => {
    fireEvent.click(screen.getByTestId("button-forgot-toggle"));
    expect(screen.getByTestId("input-password")).toBeInTheDocument();
  });
});

describe("ResetPasswordPage", () => {
  let updateSpy: ReturnType<typeof installMethodSpy>;

  beforeEach(() => {
    resetSpies();
    updateSpy = installMethodSpy(supabase.auth, "updateUser");
  });

  it("saves the new password and finishes", async () => {
    resolveWith(updateSpy, { data: {}, error: null });
    const onDone = renderResetPage(fakeSession);
    fillPasswords("new-secret", "new-secret");

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(updateSpy).toHaveBeenCalledWith({ password: "new-secret" });
  });

  it("rejects mismatched passwords without calling Supabase", () => {
    const onDone = renderResetPage(fakeSession);
    fillPasswords("new-secret", "different");

    expect(screen.getByTestId("text-reset-error")).toHaveTextContent("do not match");
    expect(updateSpy).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("stays on the page and shows Supabase errors", async () => {
    resolveWith(updateSpy, { data: null, error: { message: "Password is too weak" } });
    const onDone = renderResetPage(fakeSession);
    fillPasswords("new-secret", "new-secret");

    await waitFor(() =>
      expect(screen.getByTestId("text-reset-error")).toHaveTextContent("too weak"),
    );
    expect(onDone).not.toHaveBeenCalled();
  });

  it("explains a bad link when no recovery session exists", () => {
    const onDone = renderResetPage(null);

    expect(screen.getByTestId("text-reset-invalid")).toBeInTheDocument();
    expect(screen.queryByTestId("form-reset-password")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("button-reset-back"));
    expect(onDone).toHaveBeenCalled();
  });
});

describe("AuthProvider recovery state", () => {
  function RecoveryProbe() {
    const { passwordRecovery } = useAuth();
    return <div data-testid="recovery">{String(passwordRecovery)}</div>;
  }

  it("is on at the reset path and off elsewhere", () => {
    window.history.pushState({}, "", "/reset-password");
    const first = render(
      <AuthProvider>
        <RecoveryProbe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("recovery")).toHaveTextContent("true");
    first.unmount();

    window.history.pushState({}, "", "/");
    render(
      <AuthProvider>
        <RecoveryProbe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("recovery")).toHaveTextContent("false");
  });
});
