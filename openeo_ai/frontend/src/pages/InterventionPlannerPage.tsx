import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/auth/useAuth";
import { useSavedResults } from "@/hooks/usePlatformData";
import { formatBytes, formatDateTime } from "@/lib/utils";
import type { SavedJob, SavedJobDetail } from "@/types";
import {
  ArrowLeft,
  MapPin,
  CheckCircle2,
  AlertTriangle,
  FolderPlus,
  Eye,
  Loader2,
  Search,
  FileWarning,
} from "lucide-react";

// ── Domain-agnostic action derived from saved result metadata ──

interface DerivedAction {
  id: string;
  title: string;
  description: string;
  priority: "urgent" | "high" | "medium";
  category: "validation" | "anomaly" | "organization" | "review";
}

function deriveActions(result: SavedJob, detail: SavedJobDetail | null): DerivedAction[] {
  const actions: DerivedAction[] = [];
  let idx = 0;

  // Check validation warnings
  if (detail?.validation && !detail.validation.is_valid) {
    actions.push({
      id: `${result.save_id}-val-${idx++}`,
      title: "Review validation warnings",
      description: `This analysis has ${detail.validation.warnings.length} validation warning(s): ${detail.validation.warnings.slice(0, 2).join("; ")}`,
      priority: "urgent",
      category: "validation",
    });
  }

  // Check for extreme statistics (anomalous values)
  if (detail?.statistics) {
    const stats = detail.statistics as Record<string, number>;
    const mean = stats.mean ?? stats.Mean;
    const std = stats.std ?? stats.Std;
    const nodataPercent = stats.nodata_percent ?? stats.nodata_pct;

    if (typeof mean === "number" && typeof std === "number" && std > Math.abs(mean) * 2) {
      actions.push({
        id: `${result.save_id}-anom-${idx++}`,
        title: "Investigate high variance anomaly",
        description: `Standard deviation (${std.toFixed(4)}) exceeds twice the mean (${mean.toFixed(4)}), suggesting extreme value spread or artifacts.`,
        priority: "urgent",
        category: "anomaly",
      });
    }

    if (typeof nodataPercent === "number" && nodataPercent > 50) {
      actions.push({
        id: `${result.save_id}-nodata-${idx++}`,
        title: "Review high no-data coverage",
        description: `${nodataPercent.toFixed(1)}% of pixels are no-data. The result may have insufficient spatial coverage.`,
        priority: "high",
        category: "anomaly",
      });
    }
  }

  // No project assigned
  if (!result.project_id) {
    actions.push({
      id: `${result.save_id}-proj-${idx++}`,
      title: "Assign to project",
      description:
        "This analysis is not assigned to any project. Assigning helps organize results for reporting.",
      priority: "medium",
      category: "organization",
    });
  }

  // Small file size may indicate truncated result
  if (result.size_bytes > 0 && result.size_bytes < 1024) {
    actions.push({
      id: `${result.save_id}-size-${idx++}`,
      title: "Verify result integrity",
      description: `File size is very small (${formatBytes(result.size_bytes)}). The output may be truncated or empty.`,
      priority: "high",
      category: "validation",
    });
  }

  // No bounds metadata
  if (!result.bounds || result.bounds.length === 0) {
    actions.push({
      id: `${result.save_id}-bounds-${idx++}`,
      title: "Missing spatial bounds",
      description:
        "No geographic bounds recorded. Map visualization may not position this result correctly.",
      priority: "medium",
      category: "review",
    });
  }

  // Always offer a review action
  actions.push({
    id: `${result.save_id}-review-${idx++}`,
    title: "Open in Asset Briefing",
    description: "View full analysis details, statistics, and map preview.",
    priority: "medium",
    category: "review",
  });

  return actions;
}

function priorityLabel(priority: DerivedAction["priority"]): string {
  switch (priority) {
    case "urgent":
      return "REQUIRES ATTENTION";
    case "high":
      return "HIGH PRIORITY";
    case "medium":
      return "SCHEDULED";
  }
}

function categoryIcon(category: DerivedAction["category"]) {
  switch (category) {
    case "validation":
      return <FileWarning className="h-3 w-3" aria-hidden="true" />;
    case "anomaly":
      return <AlertTriangle className="h-3 w-3" aria-hidden="true" />;
    case "organization":
      return <FolderPlus className="h-3 w-3" aria-hidden="true" />;
    case "review":
      return <Eye className="h-3 w-3" aria-hidden="true" />;
  }
}

// ── Status badge from action counts ──

function deriveStatus(actions: DerivedAction[]): {
  label: string;
  colorClass: string;
} {
  const urgentCount = actions.filter((a) => a.priority === "urgent").length;
  const highCount = actions.filter((a) => a.priority === "high").length;
  if (urgentCount > 0) return { label: "Issues Detected", colorClass: "text-status-critical" };
  if (highCount > 0) return { label: "Needs Review", colorClass: "text-status-warn" };
  return { label: "Healthy", colorClass: "text-status-good" };
}

// ── Main Page ──

const InterventionPlannerPage: React.FC = () => {
  const navigate = useNavigate();
  const { accessToken: authToken } = useAuth();

  const savedResultsHook = useSavedResults(100);
  const savedResults = savedResultsHook.results;

  // Selected result + detail
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SavedJobDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [resolvedActions, setResolvedActions] = useState<Set<string>>(new Set());

  const headers: HeadersInit = authToken ? { Authorization: `Bearer ${authToken}` } : {};

  // Auto-select first result when data loads
  useEffect(() => {
    if (savedResults.length > 0 && !selectedId) {
      setSelectedId(savedResults[0]!.save_id);
    }
  }, [savedResults, selectedId]);

  // Fetch detail for selected result
  useEffect(() => {
    if (!selectedId || !authToken) {
      setDetail(null);
      return;
    }

    let cancelled = false;
    setDetailLoading(true);

    (async () => {
      try {
        const res = await fetch(`/saved-jobs/${selectedId}`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as SavedJobDetail;
        if (!cancelled) setDetail(data);
      } catch {
        if (!cancelled) setDetail(null);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedId, authToken]);

  const selected = useMemo(
    () => savedResults.find((r) => r.save_id === selectedId) ?? null,
    [savedResults, selectedId]
  );

  const actions = useMemo(() => {
    if (!selected) return [];
    return deriveActions(selected, detail);
  }, [selected, detail]);

  const status = useMemo(() => deriveStatus(actions), [actions]);

  const handleResolve = (actionId: string) => {
    setResolvedActions((prev) => new Set(prev).add(actionId));
  };

  const handleActionClick = (action: DerivedAction) => {
    if (action.category === "review" && selected) {
      navigate(`/dashboard/analysis/${selected.save_id}`);
      return;
    }
    if (action.category === "organization" && selected) {
      // Navigate to analysis briefing which has project assignment.
      navigate(`/dashboard/analysis/${selected.save_id}`);
      return;
    }
    // For validation/anomaly actions, mark as reviewed
    handleResolve(action.id);
  };

  // Loading state
  if (savedResultsHook.isLoading && savedResults.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <span className="font-mono text-xs text-muted-foreground">Loading saved analyses...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="screen-enter flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-4 border-b border-border px-6 py-4">
        <button
          onClick={() => navigate("/dashboard")}
          className="rounded-md p-1.5 transition-colors hover:bg-accent"
          aria-label="Go back"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-2xl font-semibold">Intervention Planner</h1>
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Analysis Review / Action Items
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          {/* Analysis Selector */}
          <div className="mb-6">
            <label className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Select Analysis
            </label>
            {savedResults.length === 0 ? (
              <div className="flex flex-col items-center py-12 text-center">
                <Search className="mb-3 h-10 w-10 text-muted-foreground/30" />
                <p className="mb-1 text-sm font-medium text-muted-foreground">
                  No saved analyses found
                </p>
                <p className="max-w-sm text-xs text-muted-foreground">
                  Run an analysis in the AI Chat workspace and save the result. It will appear here
                  for review and action planning.
                </p>
              </div>
            ) : (
              <select
                value={selectedId ?? ""}
                onChange={(e) => {
                  setSelectedId(e.target.value);
                  setResolvedActions(new Set());
                  setDetail(null);
                }}
                className="h-10 w-full max-w-md cursor-pointer rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {savedResults.map((r) => (
                  <option key={r.save_id} value={r.save_id}>
                    {r.title} -- {r.colormap} -- {formatDateTime(r.created_at)}
                  </option>
                ))}
              </select>
            )}
          </div>

          {savedResultsHook.error && (
            <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
              {savedResultsHook.error}
            </div>
          )}

          {selected && (
            <>
              {/* Status Banner */}
              <div
                className={`mb-6 flex items-center justify-between border p-3 ${
                  status.colorClass === "text-status-critical"
                    ? "border-status-critical/20 bg-status-critical/5"
                    : status.colorClass === "text-status-warn"
                      ? "border-status-warn/20 bg-status-warn/5"
                      : "border-status-good/20 bg-status-good/5"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      status.colorClass === "text-status-critical"
                        ? "animate-pulse bg-status-critical"
                        : status.colorClass === "text-status-warn"
                          ? "bg-status-warn"
                          : "bg-status-good"
                    }`}
                  />
                  <span
                    className={`font-mono text-[10px] font-bold uppercase tracking-wider ${status.colorClass}`}
                  >
                    {status.label}
                  </span>
                </div>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {actions.filter((a) => a.priority === "urgent" || a.priority === "high").length}{" "}
                  action(s) pending
                </span>
              </div>

              {/* Result Summary Card */}
              <div className="mb-6 border border-border bg-card p-4">
                <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Analysis Summary
                </span>
                <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                  <span className="font-medium text-muted-foreground">Title</span>
                  <span className="font-medium">{selected.title}</span>

                  <span className="font-medium text-muted-foreground">Created</span>
                  <span>{formatDateTime(selected.created_at)}</span>

                  <span className="font-medium text-muted-foreground">Colormap</span>
                  <span className="font-mono">{selected.colormap}</span>

                  <span className="font-medium text-muted-foreground">Size</span>
                  <span>{formatBytes(selected.size_bytes)}</span>

                  {selected.bounds && selected.bounds.length > 0 && (
                    <>
                      <span className="font-medium text-muted-foreground">Bounds</span>
                      <span className="font-mono text-xs">
                        {selected.bounds
                          .flat()
                          .map((v) => Number(v).toFixed(2))
                          .join(", ")}
                      </span>
                    </>
                  )}

                  {selected.project_id && (
                    <>
                      <span className="font-medium text-muted-foreground">Project</span>
                      <span className="font-mono text-xs">{selected.project_id}</span>
                    </>
                  )}

                  {detail?.statistics && (
                    <>
                      <span className="font-medium text-muted-foreground">Statistics</span>
                      <span className="font-mono text-xs">
                        {Object.entries(detail.statistics as Record<string, number>)
                          .filter(([, v]) => typeof v === "number")
                          .slice(0, 4)
                          .map(([k, v]) => `${k}: ${typeof v === "number" ? v.toFixed(4) : v}`)
                          .join(" | ")}
                      </span>
                    </>
                  )}
                </div>
                {detailLoading && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Loading detailed metadata...
                  </div>
                )}
              </div>

              {/* Actions Grid */}
              <div className="space-y-4">
                <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  Recommended Actions ({actions.length})
                </span>

                {actions.length === 0 && (
                  <p className="border border-border py-8 text-center text-sm text-muted-foreground">
                    No actions recommended for this analysis.
                  </p>
                )}

                {actions.map((action) => {
                  const isResolved = resolvedActions.has(action.id);
                  return (
                    <div
                      key={action.id}
                      className="relative overflow-hidden border border-border p-5 card-hover"
                    >
                      {/* Left accent bar */}
                      <div
                        className={`absolute left-0 top-0 h-full w-1 ${
                          action.priority === "urgent"
                            ? "bg-status-critical"
                            : action.priority === "high"
                              ? "bg-status-warn"
                              : "bg-border"
                        }`}
                      />

                      <div className="pl-4">
                        {/* Priority badge + category */}
                        <div className="mb-2 flex items-center justify-between">
                          <span
                            className={`rounded-md px-2 py-0.5 font-mono text-[10px] font-bold ${
                              action.priority === "urgent"
                                ? "bg-status-critical/10 text-status-critical"
                                : action.priority === "high"
                                  ? "bg-status-warn/10 text-status-warn"
                                  : "bg-accent text-muted-foreground"
                            }`}
                          >
                            {priorityLabel(action.priority)}
                          </span>
                          <span className="flex items-center gap-1 font-mono text-[10px] uppercase text-muted-foreground">
                            {categoryIcon(action.category)}
                            {action.category}
                          </span>
                        </div>

                        {/* Title + description */}
                        <h3 className="mb-1 text-sm font-medium">{action.title}</h3>
                        <p className="flex items-center gap-1 text-xs text-muted-foreground">
                          <MapPin className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                          {action.description}
                        </p>

                        {/* Action button */}
                        <div className="mt-3 flex items-center justify-end">
                          {isResolved ? (
                            <Button variant="success" className="h-9 px-4" disabled>
                              <CheckCircle2 className="mr-1 h-4 w-4" aria-hidden="true" />
                              Reviewed
                            </Button>
                          ) : (
                            <Button
                              variant={action.priority === "urgent" ? "action" : "outline"}
                              className="h-9 px-4"
                              onClick={() => handleActionClick(action)}
                            >
                              {action.category === "review" || action.category === "organization"
                                ? "Open"
                                : "Mark Reviewed"}
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default InterventionPlannerPage;
