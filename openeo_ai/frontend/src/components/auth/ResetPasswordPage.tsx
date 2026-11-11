// ABOUTME: Lets a user choose a new password after opening a Supabase recovery link.
// The link's code is exchanged by the Supabase client on load; no session means it failed.

import { useState } from "react";
import { AlertCircle, Lock } from "lucide-react";
import { supabase } from "@/lib/supabase";
import { useAuth } from "./useAuth";

const INPUT_CLASS =
  "flex h-10 w-full rounded-md border border-input bg-background pl-10 pr-3 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

interface ResetPasswordPageProps {
  /** Called when the user leaves this page, after a change or from a bad link. */
  onDone: () => void;
}

export function ResetPasswordPage({ onDone }: ResetPasswordPageProps) {
  const { session } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const { error: updateError } = await supabase.auth.updateUser({ password });
      if (updateError) {
        setError(updateError.message);
      } else {
        onDone();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection failed. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="flex min-h-screen items-center justify-center bg-background"
      data-testid="reset-password-page"
    >
      <div className="w-full max-w-md space-y-6 px-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Choose a new password
        </h1>

        {!session ? (
          <div className="space-y-4">
            <div
              role="alert"
              className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              data-testid="text-reset-invalid"
            >
              <AlertCircle className="h-4 w-4 shrink-0" />
              This reset link is invalid or has expired. Links must be opened in the browser that
              requested them. Request a new one from the sign-in page.
            </div>
            <button
              type="button"
              onClick={onDone}
              className="text-sm text-primary transition-colors hover:text-primary/80"
              data-testid="button-reset-back"
            >
              Back to sign in
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="form-reset-password">
            {error && (
              <div
                role="alert"
                aria-live="assertive"
                className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
                data-testid="text-reset-error"
              >
                <AlertCircle className="h-4 w-4 shrink-0" />
                {error}
              </div>
            )}

            <div className="space-y-1.5">
              <label htmlFor="new-password" className="text-sm font-medium text-foreground">
                New password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="new-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={INPUT_CLASS}
                  required
                  minLength={6}
                  autoComplete="new-password"
                  data-testid="input-new-password"
                />
              </div>
              <p className="text-xs text-muted-foreground">At least 6 characters</p>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="confirm-password" className="text-sm font-medium text-foreground">
                Repeat password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="confirm-password"
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className={INPUT_CLASS}
                  required
                  autoComplete="new-password"
                  data-testid="input-confirm-password"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="flex h-10 w-full items-center justify-center rounded-md bg-primary text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              data-testid="button-reset-submit"
            >
              {loading ? "Saving..." : "Save New Password"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
