import React, { useState, useMemo, useEffect } from "react";
import { scenarioVariables, simulateScenario } from "@/lib/geo/scenario-models";
import { Slider } from "@/components/ui/slider";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  FlaskConical,
  Loader2,
  MapPinned,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSavedResults } from "@/hooks/usePlatformData";
import type { SavedJob } from "@/types";

// ── Helpers ──

/**
 * Derive a 0-100 health score from a saved result.
 *
 * Strategy (in priority order):
 * 1. If vmin/vmax suggest a normalized index (e.g. NDVI in -1..1 or 0..1),
 *    map the midpoint ((vmin+vmax)/2) to 0-100.
 * 2. If the colormap name hints at a known index, use sensible scaling.
 * 3. Fallback: 50 (neutral).
 */
function deriveHealthScore(result: SavedJob): number {
  const { vmin, vmax, colormap } = result;

  // If we have value range metadata, derive from midpoint
  if (vmin != null && vmax != null && vmax !== vmin) {
    const mid = (vmin + vmax) / 2;

    // Normalized vegetation indices: typical range -1..1 or 0..1
    const isNormalizedIndex =
      colormap?.toLowerCase().includes("ndvi") ||
      colormap?.toLowerCase().includes("rdylgn") ||
      colormap?.toLowerCase().includes("greens") ||
      (vmin >= -1 && vmax <= 1);

    if (isNormalizedIndex) {
      // Map -1..1 to 0..100, or 0..1 to 0..100
      const floor = vmin < 0 ? -1 : 0;
      const ceil = 1;
      return Math.round(Math.max(0, Math.min(100, ((mid - floor) / (ceil - floor)) * 100)));
    }

    // Percentage-like ranges (0..100)
    if (vmin >= 0 && vmax <= 100) {
      return Math.round(Math.max(0, Math.min(100, mid)));
    }

    // Arbitrary range: normalize to 0-100 within vmin..vmax
    return Math.round(((mid - vmin) / (vmax - vmin)) * 100);
  }

  return 50; // neutral fallback
}

/** Format a saved result title into a short label. */
function shortLabel(title: string, maxLen = 28): string {
  if (title.length <= maxLen) return title;
  return title.slice(0, maxLen - 1) + "\u2026";
}

// ── Types ──

interface ScenarioState {
  label: string;
  variables: Record<string, number>;
}

// ── Component ──

const ScenarioSimulator: React.FC = () => {
  const savedResultsHook = useSavedResults(50);
  const savedResults = savedResultsHook.results;

  // Selected result to use as simulation baseline
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null);

  // Auto-select most recent result when data arrives
  useEffect(() => {
    if (savedResults.length > 0 && !selectedResultId) {
      setSelectedResultId(savedResults[0]!.save_id);
    }
  }, [savedResults, selectedResultId]);

  const selectedResult = useMemo(
    () => savedResults.find((r) => r.save_id === selectedResultId) ?? null,
    [savedResults, selectedResultId]
  );

  const baselineHealth = selectedResult ? deriveHealthScore(selectedResult) : 50;

  const [scenarios, setScenarios] = useState<ScenarioState[]>([
    {
      label: "Baseline",
      variables: Object.fromEntries(scenarioVariables.map((v) => [v.id, v.baseline])),
    },
    {
      label: "Scenario A",
      variables: Object.fromEntries(scenarioVariables.map((v) => [v.id, v.baseline])),
    },
    {
      label: "Scenario B",
      variables: Object.fromEntries(scenarioVariables.map((v) => [v.id, v.baseline])),
    },
  ]);
  const [activeTab, setActiveTab] = useState(0);

  const results = useMemo(
    () => scenarios.map((s) => simulateScenario(baselineHealth, s.variables)),
    [scenarios, baselineHealth]
  );

  const chartData = Array.from({ length: 12 }, (_, i) => ({
    week: `W${i + 1}`,
    Baseline: results[0]!.healthProjection[i],
    "Scenario A": results[1]!.healthProjection[i],
    "Scenario B": results[2]!.healthProjection[i],
  }));

  const updateVariable = (varId: string, value: number) => {
    setScenarios((prev) => {
      const next = [...prev];
      const current = next[activeTab]!;
      next[activeTab] = {
        ...current,
        label: current.label,
        variables: { ...current.variables, [varId]: value },
      };
      return next;
    });
  };

  const riskColors: Record<string, string> = {
    low: "text-status-good",
    moderate: "text-status-warn",
    high: "text-status-critical",
    critical: "text-status-critical",
  };

  // ── Loading state ──
  const isLoading = savedResultsHook.isLoading;

  if (isLoading && savedResults.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <span className="font-mono text-xs text-muted-foreground">Loading saved results...</span>
        </div>
      </div>
    );
  }

  // ── Empty state ──
  if (!isLoading && savedResults.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center py-16 text-center">
          <MapPinned className="mb-3 h-10 w-10 text-muted-foreground/30" />
          <p className="mb-1 text-sm font-medium text-muted-foreground">No analysis results yet</p>
          <p className="max-w-sm text-xs text-muted-foreground">
            Run an analysis in the AI Chat workspace first. The scenario simulator uses saved
            results as baseline data for projections.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Result Selector + Scenario Tabs */}
      <div className="flex flex-shrink-0 items-center gap-1 border-b border-border px-6">
        {/* Result picker */}
        <div className="mr-4 flex items-center gap-2 border-r border-border pr-4">
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Source
          </span>
          <select
            className="h-8 rounded border border-border bg-background px-2 font-mono text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            value={selectedResultId ?? ""}
            onChange={(e) => setSelectedResultId(e.target.value)}
          >
            {savedResults.map((r) => (
              <option key={r.save_id} value={r.save_id}>
                {shortLabel(r.title)}
              </option>
            ))}
          </select>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={savedResultsHook.refetch}>
            <RefreshCw className="h-3 w-3" />
          </Button>
        </div>

        {/* Scenario tabs */}
        {scenarios.map((s, i) => (
          <button
            key={i}
            onClick={() => setActiveTab(i)}
            className={`border-b-2 px-4 py-3 text-sm font-medium transition-colors ${
              activeTab === i
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <FlaskConical className="mr-1.5 inline h-3.5 w-3.5" />
            {s.label}
          </button>
        ))}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Variable Controls */}
        <div className="w-72 flex-shrink-0 overflow-auto border-r border-border bg-card p-6">
          {/* Baseline info from selected result */}
          {selectedResult && (
            <div className="mb-6 border-b border-border pb-4">
              <span className="mb-2 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                Baseline Source
              </span>
              <p className="mb-1 line-clamp-2 text-xs font-medium">{selectedResult.title}</p>
              <div className="space-y-0.5 font-mono text-[10px] text-muted-foreground">
                {selectedResult.colormap && (
                  <div className="flex justify-between">
                    <span>Colormap</span>
                    <span className="font-medium text-foreground">{selectedResult.colormap}</span>
                  </div>
                )}
                {selectedResult.vmin != null && selectedResult.vmax != null && (
                  <div className="flex justify-between">
                    <span>Range</span>
                    <span>
                      {selectedResult.vmin.toFixed(3)} - {selectedResult.vmax.toFixed(3)}
                    </span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span>Health Score</span>
                  <span className="font-medium text-foreground">{baselineHealth}/100</span>
                </div>
              </div>
            </div>
          )}

          <span className="mb-4 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            {scenarios[activeTab]!.label} — Variables
          </span>

          {savedResultsHook.error && (
            <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-[10px] text-destructive">
              {savedResultsHook.error}
            </div>
          )}

          {scenarioVariables.map((v) => (
            <div key={v.id} className="mb-6">
              <div className="mb-2 flex justify-between">
                <label className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  {v.label}
                </label>
                <span className="font-mono text-sm font-medium">
                  {scenarios[activeTab]!.variables[v.id]}
                  {v.unit}
                </span>
              </div>
              <Slider
                value={[scenarios[activeTab]!.variables[v.id] ?? v.baseline]}
                onValueChange={(val) => updateVariable(v.id, val[0] ?? v.baseline)}
                min={v.min}
                max={v.max}
                step={v.step}
              />
              <div className="mt-1 flex justify-between">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {v.min}
                  {v.unit}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {v.max}
                  {v.unit}
                </span>
              </div>
            </div>
          ))}
        </div>

        {/* Right: Results */}
        <div className="flex-1 overflow-auto p-6">
          {/* Comparison Cards */}
          <div className="mb-8 grid grid-cols-3 gap-4">
            {results.map((r, i) => (
              <div
                key={i}
                className={`border p-4 ${activeTab === i ? "border-primary" : "border-border"}`}
              >
                <span className="mb-2 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  {scenarios[i]!.label}
                </span>
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-4xl font-semibold">
                    {Math.round(r.healthProjection[11]!)}
                  </span>
                  <span className="font-mono text-lg text-muted-foreground">/100</span>
                </div>
                <div className="mt-3 space-y-1">
                  <div className="flex justify-between">
                    <span className="text-xs text-muted-foreground">Yield Delta</span>
                    <span
                      className={`font-mono text-sm font-medium ${r.yieldDelta >= 0 ? "text-status-good" : "text-status-critical"}`}
                    >
                      {r.yieldDelta >= 0 ? (
                        <TrendingUp className="mr-1 inline h-3 w-3" />
                      ) : (
                        <TrendingDown className="mr-1 inline h-3 w-3" />
                      )}
                      ${Math.abs(r.yieldDelta).toLocaleString()}/HA
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-xs text-muted-foreground">Financial Impact</span>
                    <span
                      className={`font-mono text-sm font-medium ${r.financialImpact >= 0 ? "text-status-good" : "text-status-critical"}`}
                    >
                      ${Math.abs(r.financialImpact).toLocaleString()}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-xs text-muted-foreground">Risk Level</span>
                    <span
                      className={`font-mono text-xs font-medium uppercase ${riskColors[r.riskLevel] ?? ""}`}
                    >
                      {r.riskLevel}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Trajectory Chart */}
          <div className="border border-border p-4">
            <span className="mb-4 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              12-Week Health Score Projection
            </span>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis
                  dataKey="week"
                  tick={{ fontSize: 11, fontFamily: "JetBrains Mono" }}
                  stroke="hsl(var(--muted-foreground))"
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fontSize: 11, fontFamily: "JetBrains Mono" }}
                  stroke="hsl(var(--muted-foreground))"
                />
                <Tooltip
                  contentStyle={{
                    fontFamily: "JetBrains Mono",
                    fontSize: 12,
                    border: "1px solid hsl(var(--border))",
                    borderRadius: 2,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="Baseline"
                  stroke="hsl(var(--primary))"
                  fill="hsl(var(--primary) / 0.1)"
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="Scenario A"
                  stroke="hsl(var(--status-warn))"
                  fill="hsl(var(--status-warn) / 0.1)"
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="Scenario B"
                  stroke="hsl(var(--status-critical))"
                  fill="hsl(var(--status-critical) / 0.1)"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>

            {/* Legend */}
            <div className="mt-3 flex items-center justify-center gap-6">
              <div className="flex items-center gap-2">
                <div className="h-0.5 w-3 bg-primary" />
                <span className="font-mono text-[10px] text-muted-foreground">Baseline</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="h-0.5 w-3 bg-status-warn" />
                <span className="font-mono text-[10px] text-muted-foreground">Scenario A</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="h-0.5 w-3 bg-status-critical" />
                <span className="font-mono text-[10px] text-muted-foreground">Scenario B</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ScenarioSimulator;
