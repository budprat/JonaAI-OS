// ABOUTME: 5-step onboarding wizard for new users (Phase 13).
// Triggered on first login when profile.onboarding_completed is false.

import { useState, useCallback } from "react";
import { CheckCircle2, ArrowRight, X } from "lucide-react";
import { useAuth } from "@/components/auth/useAuth";

interface OnboardingWizardProps {
  onComplete: () => void;
  onSkip: () => void;
}

const EXPERTISE_OPTIONS = [
  "agriculture",
  "forestry",
  "urban-planning",
  "climate",
  "disaster-response",
  "mining",
  "water-resources",
  "marine",
];

const STEPS = [
  "Welcome",
  "First Analysis",
  "Explore Dashboard",
  "Save & Share",
  "Join Community",
];

export function OnboardingWizard({ onComplete, onSkip }: OnboardingWizardProps) {
  const { accessToken } = useAuth();
  const [step, setStep] = useState(0);
  const [displayName, setDisplayName] = useState("");
  const [expertise, setExpertise] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const saveProgress = useCallback(
    async (nextStep: number, completed: boolean) => {
      if (!accessToken) return;
      setSaving(true);
      setError("");
      try {
        // Save profile basics on step 0
        if (step === 0 && (displayName || expertise.length > 0)) {
          await fetch("/api/v1/profiles/me", {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${accessToken}`,
            },
            body: JSON.stringify({ display_name: displayName, expertise }),
          });
        }
        // Update onboarding progress
        await fetch("/api/v1/profiles/me/onboarding", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${accessToken}`,
          },
          body: JSON.stringify({ step: nextStep, completed, data: {} }),
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Save failed");
      } finally {
        setSaving(false);
      }
    },
    [accessToken, step, displayName, expertise],
  );

  const handleNext = useCallback(async () => {
    const next = step + 1;
    if (next >= STEPS.length) {
      await saveProgress(next, true);
      onComplete();
    } else {
      await saveProgress(next, false);
      setStep(next);
    }
  }, [step, saveProgress, onComplete]);

  const toggleExpertise = (item: string) => {
    setExpertise((cur) =>
      cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item],
    );
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
      data-testid="onboarding-wizard"
    >
      <div className="relative w-full max-w-lg rounded-lg border border-border bg-card p-8 shadow-lg">
        <button
          type="button"
          onClick={onSkip}
          className="absolute right-4 top-4 text-muted-foreground hover:text-foreground"
          data-testid="button-skip-onboarding"
          aria-label="Skip onboarding"
        >
          <X className="h-5 w-5" />
        </button>

        {/* Progress bar */}
        <div className="mb-6 flex items-center gap-2">
          {STEPS.map((label, i) => (
            <div
              key={label}
              className={`h-1 flex-1 rounded-full ${
                i <= step ? "bg-primary" : "bg-muted"
              }`}
            />
          ))}
        </div>

        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Step {step + 1} of {STEPS.length}
        </p>
        <h2 className="mb-4 text-2xl font-semibold tracking-tight" data-testid="text-step-title">
          {STEPS[step]}
        </h2>

        {/* Step content */}
        {step === 0 && (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Welcome to Jona AI. Let's set up your profile.
            </p>
            <div className="space-y-1.5">
              <label htmlFor="display-name" className="text-sm font-medium">
                Display name
              </label>
              <input
                id="display-name"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="How you'd like to appear to others"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                data-testid="input-display-name"
              />
            </div>
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Your expertise areas</p>
              <div className="flex flex-wrap gap-2">
                {EXPERTISE_OPTIONS.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => toggleExpertise(item)}
                    className={`rounded-full border px-3 py-1 text-xs capitalize ${
                      expertise.includes(item)
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input bg-background text-foreground hover:bg-accent"
                    }`}
                    data-testid={`button-expertise-${item}`}
                  >
                    {item.replace("-", " ")}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>
              Run your first analysis by asking a question in natural language.
              Try something like:
            </p>
            <div className="rounded-md bg-muted p-3 font-mono text-xs">
              "Show me vegetation health around Kharg Island for the last 3
              months"
            </div>
            <p>Jona AI will build the OpenEO pipeline and execute it for you.</p>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>The Geo Hub has 12 specialized dashboards:</p>
            <ul className="space-y-1 text-xs">
              <li>• Portfolio: all your projects at a glance</li>
              <li>• Live Monitoring: real-time satellite pass tracking</li>
              <li>• Marketplace: community-shared workflow templates</li>
              <li>• Community: active challenges + leaderboards</li>
              <li>• And more…</li>
            </ul>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>
              Save analyses to projects, share them with a link, or publish
              them to the community marketplace.
            </p>
            <p>
              All your data is isolated to your account — use Row-Level Security
              policies in Supabase.
            </p>
          </div>
        )}

        {step === 4 && (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>
              Join active challenges to compete on the leaderboard, or browse
              the marketplace for workflows other users have shared.
            </p>
            <div className="flex items-center gap-2 rounded-md bg-primary/10 p-3">
              <CheckCircle2 className="h-4 w-4 text-primary" />
              <span className="text-xs text-primary">You're all set!</span>
            </div>
          </div>
        )}

        {error && (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {error}
          </p>
        )}

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onSkip}
            className="text-sm text-muted-foreground hover:text-foreground"
            data-testid="button-skip"
          >
            Skip
          </button>
          <button
            type="button"
            onClick={handleNext}
            disabled={saving}
            className="flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            data-testid="button-next"
          >
            {step === STEPS.length - 1 ? "Finish" : "Next"}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
