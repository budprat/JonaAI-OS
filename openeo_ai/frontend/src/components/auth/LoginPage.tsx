// ABOUTME: Login, sign-up and forgot-password page using Supabase Auth.
// Replaces the legacy /api/auth/login fetch-based flow with supabase.auth.signInWithPassword.
// Auth state is managed by AuthProvider — no onLogin callback needed.

import { useState } from "react";
import { Lock, Mail, AlertCircle, Globe2, ExternalLink, CheckCircle2 } from "lucide-react";
import { supabase } from "@/lib/supabase";
import { PASSWORD_RESET_PATH } from "./AuthProvider";
import {
  buildProxyAuthSession,
  clearProxyAuthSession,
  isNetworkAuthError,
  writeProxyAuthSession,
  type ProxySessionPayload,
} from "./proxySession";

type Mode = "signin" | "signup" | "forgot";

type ProxyAuthResponse = ProxySessionPayload & {
  code?: number | string;
  id?: string;
  error?: string;
  error_code?: string;
  msg?: string;
  message?: string;
  error_description?: string;
};

async function proxySupabaseAuth(
  mode: Exclude<Mode, "forgot">,
  email: string,
  password: string,
): Promise<ProxyAuthResponse> {
  const response = await fetch(`/api/supabase-auth/${mode === "signin" ? "signin" : "signup"}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const payload = (await response.json().catch(() => ({}))) as ProxyAuthResponse;
  if (!response.ok) {
    throw new Error(
      authErrorMessage({
        ...payload,
        message:
          payload.error_description ||
          payload.message ||
          payload.msg ||
          payload.error ||
          `Authentication failed with status ${response.status}`,
      }),
    );
  }
  return payload;
}

function authErrorMessage(payload: ProxyAuthResponse): string {
  const rawMessage =
    payload.error_description ||
    payload.message ||
    payload.msg ||
    payload.error ||
    "Authentication failed. Try again.";

  if (
    payload.error_code === "over_email_send_rate_limit" ||
    rawMessage.toLowerCase().includes("email rate limit")
  ) {
    return "Too many sign-up emails were requested. Wait a few minutes before trying again, or sign in if this account already exists.";
  }

  if (
    payload.error_code === "supabase_auth_unreachable" ||
    rawMessage === "Supabase auth request failed"
  ) {
    return "Could not reach Supabase Auth. Check your connection and try again.";
  }

  return rawMessage;
}

async function activateProxyAuthSession(payload: ProxyAuthResponse): Promise<boolean> {
  const proxySession = buildProxyAuthSession(payload);
  if (!proxySession) return false;

  try {
    const { error } = await supabase.auth.setSession({
      access_token: proxySession.access_token,
      refresh_token: proxySession.refresh_token,
    });
    if (!error) {
      clearProxyAuthSession();
      return true;
    }
    if (!isNetworkAuthError(error)) {
      throw new Error(error.message);
    }
  } catch (err) {
    if (!isNetworkAuthError(err)) throw err;
  }

  writeProxyAuthSession(proxySession);
  return true;
}

export function LoginPage() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInfo("");
    setLoading(true);

    try {
      if (mode === "forgot") {
        const { error: resetError } = await supabase.auth.resetPasswordForEmail(email.trim(), {
          redirectTo: `${window.location.origin}${PASSWORD_RESET_PATH}`,
        });
        if (resetError?.code === "over_email_send_rate_limit" || resetError?.status === 429) {
          setError("Too many reset emails were requested. Wait a while before trying again.");
        } else if (resetError) {
          setError(resetError.message);
        } else {
          // Same wording whether or not the account exists, so this form can't
          // be used to discover registered emails.
          setInfo(
            "If an account exists for that email, a reset link is on its way. Open it in this browser.",
          );
        }
      } else if (mode === "signin") {
        try {
          const { error: signInError } = await supabase.auth.signInWithPassword({
            email,
            password,
          });
          if (signInError) {
            if (isNetworkAuthError(signInError)) {
              const payload = await proxySupabaseAuth("signin", email, password);
              if (await activateProxyAuthSession(payload)) {
                setError("");
              } else {
                setError(authErrorMessage(payload));
              }
            } else {
              setError(signInError.message);
            }
          } else {
            clearProxyAuthSession();
          }
        } catch (err) {
          if (!isNetworkAuthError(err)) throw err;
          const payload = await proxySupabaseAuth("signin", email, password);
          if (await activateProxyAuthSession(payload)) {
            setError("");
          } else {
            setError(authErrorMessage(payload));
          }
        }
        // On success, AuthProvider's onAuthStateChange picks up the session
        // and the App component automatically renders the authenticated UI.
      } else {
        try {
          const { data, error: signUpError } = await supabase.auth.signUp({
            email,
            password,
          });
          if (signUpError) {
            if (isNetworkAuthError(signUpError)) {
              const payload = await proxySupabaseAuth("signup", email, password);
              if (await activateProxyAuthSession(payload)) {
                setError("");
              } else if (payload.id || payload.user) {
                setInfo("Check your email for a confirmation link to complete sign-up.");
              } else {
                setError(authErrorMessage(payload));
              }
            } else {
              setError(signUpError.message);
            }
          } else if (data.user && !data.session) {
            setInfo("Check your email for a confirmation link to complete sign-up.");
          } else {
            clearProxyAuthSession();
          }
        } catch (err) {
          if (!isNetworkAuthError(err)) throw err;
          const payload = await proxySupabaseAuth("signup", email, password);
          if (await activateProxyAuthSession(payload)) {
            setError("");
          } else if (payload.id || payload.user) {
            setInfo("Check your email for a confirmation link to complete sign-up.");
          } else {
            setError(authErrorMessage(payload));
          }
        }
        // If email confirmation is disabled, signUp returns a session and
        // AuthProvider auto-redirects to the app.
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection failed. Try again.");
    } finally {
      setLoading(false);
    }
  };

  const isSignIn = mode === "signin";
  const isForgot = mode === "forgot";

  return (
    <div
      className="flex min-h-screen items-center justify-center bg-background"
      data-testid="login-page"
    >
      <div className="w-full max-w-md space-y-6 px-6">
        <div className="flex flex-col items-center gap-3">
          <div className="w-full overflow-hidden rounded-xl shadow-lg">
            <video
              autoPlay
              loop
              muted
              playsInline
              className="w-full"
              data-testid="video-login-promo"
            >
              <source src="/jona-ai-promo.mp4" type="video/mp4" />
            </video>
          </div>
          <div className="flex items-center gap-3">
            <img
              src="/jonaai.png"
              alt="Jona AI"
              className="h-10 w-10 rounded-lg"
              data-testid="img-login-logo"
            />
            <div>
              <h1
                className="text-2xl font-semibold tracking-tight text-foreground"
                data-testid="text-login-title"
              >
                Jona AI
              </h1>
              <p className="text-sm text-muted-foreground">Chat with the Planet</p>
            </div>
          </div>
        </div>

        {/* Mode toggle */}
        <div
          className="flex rounded-md border border-input bg-background p-1"
          data-testid="auth-mode-toggle"
        >
          <button
            type="button"
            onClick={() => {
              setMode("signin");
              setError("");
              setInfo("");
            }}
            className={`flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              isSignIn || isForgot
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            data-testid="button-mode-signin"
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("signup");
              setError("");
              setInfo("");
            }}
            className={`flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "signup"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            data-testid="button-mode-signup"
          >
            Sign Up
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4" data-testid="form-login">
          {error && (
            <div
              role="alert"
              aria-live="assertive"
              className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              data-testid="text-login-error"
            >
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {info && (
            <div
              role="status"
              aria-live="polite"
              className="flex items-center gap-2 rounded-md bg-primary/10 px-3 py-2 text-sm text-primary"
              data-testid="text-login-info"
            >
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              {info}
            </div>
          )}

          <div className="space-y-1.5">
            <label htmlFor="email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background pl-10 pr-3 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="you@example.com"
                required
                autoComplete="email"
                data-testid="input-email"
              />
            </div>
          </div>

          {!isForgot && (
          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium text-foreground">
              Password
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background pl-10 pr-3 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder={isSignIn ? "Enter password" : "Choose a password"}
                required
                minLength={isSignIn ? undefined : 6}
                autoComplete={isSignIn ? "current-password" : "new-password"}
                data-testid="input-password"
              />
            </div>
            {!isSignIn && <p className="text-xs text-muted-foreground">At least 6 characters</p>}
          </div>
          )}

          {(isSignIn || isForgot) && (
            <button
              type="button"
              onClick={() => {
                setMode(isForgot ? "signin" : "forgot");
                setError("");
                setInfo("");
              }}
              className="text-sm text-primary transition-colors hover:text-primary/80"
              data-testid="button-forgot-toggle"
            >
              {isForgot ? "Back to sign in" : "Forgot password?"}
            </button>
          )}

          <button
            type="submit"
            disabled={loading}
            className="flex h-10 w-full items-center justify-center gap-2 rounded-md bg-primary text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
            data-testid="button-login"
          >
            {loading ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
            ) : (
              <>
                <Globe2 className="h-4 w-4" />
                {isForgot ? "Send Reset Link" : isSignIn ? "Sign In" : "Create Account"}
              </>
            )}
          </button>
        </form>

        <a
          href="https://site.jonaai.com"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 text-sm text-primary transition-colors hover:text-primary/80"
          data-testid="link-main-webpage"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Go to Main Webpage
        </a>
      </div>
    </div>
  );
}
