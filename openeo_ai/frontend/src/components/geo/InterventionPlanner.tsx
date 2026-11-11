import React, { useState } from "react";
import { format } from "date-fns";
import { Button } from "@/components/ui/button";
import { contractors, type RecommendedAction } from "@/lib/geo/mock-data";
import { CalendarIcon, Loader2 } from "lucide-react";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface InterventionPlannerProps {
  action: RecommendedAction;
  assetName: string;
  onDeploy: (actionId: string) => void;
  onClose: () => void;
}

export const InterventionPlanner: React.FC<InterventionPlannerProps> = ({
  action,
  assetName,
  onDeploy,
  onClose,
}) => {
  const [contractor, setContractor] = useState("");
  const [budget, setBudget] = useState(action.estimatedCost.toFixed(2));
  const [date, setDate] = useState<Date>();
  const [notes, setNotes] = useState("");
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState("");

  const canSubmit = contractor && budget && date && !deploying;

  const priorityColor =
    action.priority === "urgent"
      ? "text-status-critical"
      : action.priority === "high"
        ? "text-status-warn"
        : "text-muted-foreground";
  const priorityLabel =
    action.priority === "urgent"
      ? "Immediate"
      : action.priority === "high"
        ? "This Week"
        : "Scheduled";

  const handleDeploy = () => {
    const budgetNum = parseFloat(budget);
    if (budgetNum > 5000) {
      setError("Exceeds remaining Q3 maintenance budget. Reduce to $5,000 or less.");
      return;
    }
    setError("");
    setDeploying(true);
    setTimeout(() => {
      onDeploy(action.id);
      toast.success("Intervention deployed successfully. Contractor notified.");
    }, 1200);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="intervention-title"
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-foreground/50 backdrop-blur-[2px]" onClick={onClose} />

      {/* Modal */}
      <div className="relative z-10 flex max-h-[90vh] w-full max-w-[600px] flex-col border border-border bg-background">
        {/* Header */}
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-6 py-4">
          <h2 id="intervention-title" className="text-xl font-semibold">
            Deploy Intervention
          </h2>
          <button
            onClick={onClose}
            className="flex h-6 w-6 items-center justify-center text-xl leading-none text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto p-6" style={{ overscrollBehavior: "contain" }}>
          {/* Risk Forecast */}
          <div className="bg-status-critical/5 border-status-critical/20 mb-6 flex items-center justify-between border p-3">
            <div className="flex items-center gap-2">
              <span className="bg-status-critical h-2 w-2 animate-pulse rounded-full" />
              <span className="text-status-critical font-mono text-[10px] font-bold uppercase tracking-wider">
                Critical Fault Detected
              </span>
            </div>
            <span className="text-status-critical font-mono text-[10px] font-bold">Risk: 88%</span>
          </div>

          {/* Intervention Summary */}
          <div className="mb-6 border border-border bg-card p-4">
            <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Intervention Summary
            </span>
            <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <span className="font-medium">Action</span>
              <span>{action.title}</span>
              <span className="font-medium">Target</span>
              <span>
                {assetName} — {action.target}
              </span>
              <span className="font-medium">Priority</span>
              <span className={`font-mono text-sm ${priorityColor}`}>{priorityLabel}</span>
            </div>
          </div>

          {/* Financial Recovery Preview */}
          <div className="mb-6 bg-primary p-4 text-primary-foreground">
            <span className="mb-3 block font-mono text-[9px] uppercase tracking-wider opacity-60">
              Financial Recovery
            </span>
            <div className="grid grid-cols-3 gap-4 text-center">
              <div>
                <span className="font-mono text-[9px] uppercase opacity-50">Projected ROI</span>
                <div className="text-status-good mt-1 font-mono text-lg font-bold">
                  +
                  {Math.round(
                    ((action.estimatedCost * 4.12 - action.estimatedCost) / action.estimatedCost) *
                      100
                  )}
                  %
                </div>
              </div>
              <div>
                <span className="font-mono text-[9px] uppercase opacity-50">Avoided Loss</span>
                <div className="mt-1 font-mono text-lg font-bold">
                  $
                  {(action.estimatedCost * 4.12).toLocaleString(undefined, {
                    maximumFractionDigits: 0,
                  })}
                </div>
              </div>
              <div>
                <span className="font-mono text-[9px] uppercase opacity-50">Cost</span>
                <div className="text-status-critical mt-1 font-mono text-lg font-bold">
                  ${action.estimatedCost.toLocaleString()}
                </div>
              </div>
            </div>
          </div>

          {/* Form */}
          <div className="space-y-4">
            {/* Contractor */}
            <div>
              <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Assign Contractor
              </label>
              <select
                value={contractor}
                onChange={(e) => setContractor(e.target.value)}
                className="h-10 w-full cursor-pointer rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="">Select contractor...</option>
                {contractors.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>

            {/* Budget Cap */}
            <div>
              <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Budget Cap
              </label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">
                  $
                </span>
                <input
                  type="text"
                  inputMode="decimal"
                  value={budget}
                  onChange={(e) => {
                    setBudget(e.target.value);
                    setError("");
                  }}
                  className="h-10 w-full rounded-md border border-border bg-background pl-7 pr-3 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              {error && <p className="text-status-critical mt-1 text-xs">{error}</p>}
            </div>

            {/* Execute By */}
            <div>
              <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Execute By
              </label>
              <Popover>
                <PopoverTrigger asChild>
                  <button
                    className={cn(
                      "flex h-10 w-full items-center gap-2 rounded-md border border-border bg-background px-3 text-left text-sm focus:outline-none focus:ring-1 focus:ring-ring",
                      !date && "text-muted-foreground"
                    )}
                  >
                    <CalendarIcon className="h-4 w-4" aria-hidden="true" />
                    {date ? format(date, "PPP") : "Select deadline..."}
                  </button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="single"
                    selected={date}
                    onSelect={setDate}
                    initialFocus
                    className="pointer-events-auto p-3"
                  />
                </PopoverContent>
              </Popover>
            </div>

            {/* Notes */}
            <div>
              <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Notes (Optional)
              </label>
              <textarea
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Additional instructions..."
                className="w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>

            {/* Logistic Readiness */}
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Logistic Readiness
                </label>
                <span className="text-status-good font-mono text-[10px] font-bold">Optimized</span>
              </div>
              <div className="h-1.5 w-full bg-muted">
                <div
                  className="bg-status-good h-1.5 transition-all duration-500"
                  style={{ width: "92%" }}
                />
              </div>
            </div>
          </div>

          {/* Executive Protocol Notice */}
          <div className="mt-4 flex items-start gap-3 border border-dashed border-border p-3">
            <svg
              className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
              />
            </svg>
            <div>
              <p className="text-xs font-bold">Executive Protocol Required</p>
              <p className="mt-0.5 text-[10px] text-muted-foreground">
                Deploying this intervention requires Level 4 authorization signature.
              </p>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 px-6 pb-6">
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="h-12 flex-1"
              onClick={() => {
                toast.info("Team notification sent.");
              }}
            >
              Notify Team
            </Button>
            <Button
              variant="action"
              className="h-12 flex-[2]"
              onClick={handleDeploy}
              disabled={!canSubmit}
            >
              {deploying ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                  Deploying...
                </>
              ) : (
                "Deploy Intervention"
              )}
            </Button>
          </div>
          <button
            onClick={onClose}
            className="mt-2 w-full py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Cancel
          </button>

          {/* Footer Status */}
          <div className="mt-4 flex items-center justify-between border-t border-border pt-3 font-mono text-[9px] text-muted-foreground">
            <div className="flex gap-4">
              <span className="uppercase tracking-wider">Est. completion: 14 days</span>
              <span className="flex items-center gap-1">
                <span className="bg-status-good h-1.5 w-1.5 rounded-full" />
                <span className="uppercase tracking-wider">Satellite Sync: Live</span>
              </span>
            </div>
            <span className="uppercase tracking-wider">Asset: {assetName}</span>
          </div>
        </div>
      </div>
    </div>
  );
};
