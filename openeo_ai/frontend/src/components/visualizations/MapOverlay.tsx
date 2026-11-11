/**
 * MapOverlay — Reusable overlay cards for raster map displays.
 *
 * Shows index-specific legend, interpretation grade card, NaN warning,
 * and statistics panel on top of rendered raster images.
 * Generic for any spectral index and domain.
 */
import { AlertTriangle, Info, TrendingDown, TrendingUp } from "lucide-react";
import type { SavedJobInterpretation } from "@/types";

// ── Index-specific legend labels (domain_knowledge.py thresholds) ──

const INDEX_LEGEND_LABELS: Record<
  string,
  Array<{ label: string; range: string; color: string }>
> = {
  NDVI: [
    { label: "Water/Shadow", range: "< 0", color: "#a50026" },
    { label: "Bare Soil", range: "0 - 0.1", color: "#f46d43" },
    { label: "Sparse", range: "0.1 - 0.3", color: "#fee08b" },
    { label: "Moderate", range: "0.3 - 0.6", color: "#a6d96a" },
    { label: "Dense", range: "0.6+", color: "#006837" },
  ],
  EVI: [
    { label: "Non-vegetated", range: "< 0.1", color: "#f46d43" },
    { label: "Sparse", range: "0.1 - 0.25", color: "#fee08b" },
    { label: "Moderate", range: "0.25 - 0.4", color: "#a6d96a" },
    { label: "Dense", range: "0.4+", color: "#006837" },
  ],
  NDWI: [
    { label: "Land", range: "< 0", color: "#d73027" },
    { label: "Boundary", range: "0 - 0.3", color: "#fee08b" },
    { label: "Water", range: "0.3 - 0.6", color: "#74add1" },
    { label: "Deep Water", range: "0.6+", color: "#313695" },
  ],
  MNDWI: [
    { label: "Land", range: "< 0", color: "#d73027" },
    { label: "Mixed", range: "0 - 0.2", color: "#fee08b" },
    { label: "Water", range: "0.2+", color: "#313695" },
  ],
  NDBI: [
    { label: "Non-built", range: "< -0.1", color: "#1a9850" },
    { label: "Mixed", range: "-0.1 - 0.1", color: "#fee08b" },
    { label: "Low-density", range: "0.1 - 0.3", color: "#f46d43" },
    { label: "Urban/Dense", range: "0.3+", color: "#a50026" },
  ],
  NBR: [
    { label: "High Severity", range: "< -0.25", color: "#67001f" },
    { label: "Moderate", range: "-0.25 - -0.1", color: "#d6604d" },
    { label: "Low Severity", range: "-0.1 - 0.1", color: "#fee08b" },
    { label: "Unburned", range: "0.1+", color: "#1a9850" },
  ],
  BSI: [
    { label: "Vegetated", range: "< -0.1", color: "#006837" },
    { label: "Mixed", range: "-0.1 - 0.1", color: "#fee08b" },
    { label: "Bare Soil", range: "0.1+", color: "#d73027" },
  ],
  NDSI: [
    { label: "Non-snow", range: "< 0.2", color: "#a6611a" },
    { label: "Mixed", range: "0.2 - 0.4", color: "#dfc27d" },
    { label: "Snow/Ice", range: "0.4+", color: "#f5f5f5" },
  ],
  NDMI: [
    { label: "Dry", range: "< -0.1", color: "#d73027" },
    { label: "Moderate", range: "-0.1 - 0.2", color: "#fee08b" },
    { label: "Moist", range: "0.2 - 0.4", color: "#74add1" },
    { label: "Wet", range: "0.4+", color: "#313695" },
  ],
};

function getIndexLabels(index: string | null | undefined) {
  if (!index) return null;
  return INDEX_LEGEND_LABELS[index.toUpperCase()] || null;
}

// ── Components ──

interface IndexLegendProps {
  index: string | null | undefined;
  colormap: string;
  /** If provided, uses actual data-derived categories instead of static labels */
  categoryBreakdown?: Array<{
    category: string;
    range: string;
    est_area_pct: number;
    assessment: string;
  }>;
}

/** Index-specific legend with category labels — uses real data when available */
export function IndexLegend({ index, colormap, categoryBreakdown }: IndexLegendProps) {
  const staticLabels = getIndexLabels(index);

  // If we have real category breakdown, use that (data-driven, context-aware)
  if (categoryBreakdown && categoryBreakdown.length > 0) {
    // Map category names to colors from static labels if available
    // Uses fuzzy matching: exact match first, then partial/contains match
    const staticEntries = (staticLabels || []).map((l) => ({
      key: l.label.toLowerCase(),
      color: l.color,
    }));
    function findColor(categoryName: string, fallbackIndex: number): string {
      const lower = categoryName.toLowerCase();
      // Exact match
      const exact = staticEntries.find((e) => e.key === lower);
      if (exact) return exact.color;
      // Category contains a static label (e.g., "Water Boundary" contains "boundary")
      const contains = staticEntries.find((e) => lower.includes(e.key) || e.key.includes(lower));
      if (contains) return contains.color;
      // Fallback
      const defaultColors = ["#4575b4", "#74add1", "#fee08b", "#f46d43", "#d73027"];
      return defaultColors[fallbackIndex % defaultColors.length] || "#888";
    }

    return (
      <div className="rounded-lg border border-white/20 bg-black/75 px-3 py-2 backdrop-blur-sm">
        <div className="mb-1.5 flex items-center gap-1.5">
          <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-white/90">
            {index}
          </span>
          <span className="font-mono text-[9px] text-white/50">{colormap}</span>
        </div>
        <div className="space-y-0.5">
          {categoryBreakdown.map((cat, i) => {
            const isPresent = cat.est_area_pct > 0;
            const color = findColor(cat.category, i);
            return (
              <div
                key={cat.category}
                className={`flex items-center gap-2 text-[10px] ${isPresent ? "" : "opacity-30"}`}
              >
                <div
                  className="h-2.5 w-5 rounded-sm"
                  style={{ backgroundColor: color }}
                />
                <span className={isPresent ? "text-white/90" : "text-white/40"}>
                  {cat.category}
                </span>
                <span className="ml-auto font-mono text-white/50">
                  {isPresent ? `~${cat.est_area_pct}%` : "0%"}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  // Fallback to static labels if no interpretation data
  if (!staticLabels) return null;

  return (
    <div className="rounded-lg border border-white/20 bg-black/75 px-3 py-2 backdrop-blur-sm">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-white/90">
          {index}
        </span>
        <span className="font-mono text-[9px] text-white/50">{colormap}</span>
      </div>
      <div className="space-y-0.5">
        {staticLabels.map((l) => (
          <div key={l.label} className="flex items-center gap-2 text-[10px]">
            <div
              className="h-2.5 w-5 rounded-sm"
              style={{ backgroundColor: l.color }}
            />
            <span className="text-white/80">{l.label}</span>
            <span className="ml-auto font-mono text-white/50">{l.range}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface InterpretationBadgeProps {
  interpretation: SavedJobInterpretation;
  compact?: boolean;
}

/** Floating interpretation grade badge for map overlay */
export function InterpretationBadge({
  interpretation,
  compact = false,
}: InterpretationBadgeProps) {
  const gradeColor =
    interpretation.grade === "A" || interpretation.grade === "B"
      ? "bg-green-600"
      : interpretation.grade === "C"
        ? "bg-amber-500"
        : "bg-red-600";

  if (compact) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-white/20 bg-black/75 px-3 py-1.5 backdrop-blur-sm">
        <span
          className={`flex h-7 w-7 items-center justify-center rounded text-sm font-bold text-white ${gradeColor}`}
        >
          {interpretation.grade || "?"}
        </span>
        <div>
          <div className="text-[10px] font-medium text-white/90">
            {interpretation.mean_category || "Analysis"}
          </div>
          <div className="font-mono text-[9px] text-white/60">
            {interpretation.grade_score ?? "?"}/100
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[200px] rounded-lg border border-white/20 bg-black/80 px-3 py-2 backdrop-blur-sm">
      <div className="mb-2 flex items-center gap-2">
        <span
          className={`flex h-8 w-8 items-center justify-center rounded text-sm font-bold text-white ${gradeColor}`}
        >
          {interpretation.grade || "?"}
        </span>
        <div>
          <div className="text-xs font-medium text-white/90">
            {interpretation.mean_category || "Analysis Result"}
            {interpretation.index && (
              <span className="ml-1 font-mono text-[9px] text-white/50">({interpretation.index})</span>
            )}
          </div>
          <div className="font-mono text-[10px] text-white/60">
            Score: {interpretation.grade_score ?? "?"}/100
            {interpretation.domain && ` | ${interpretation.domain}`}
          </div>
        </div>
      </div>
      {interpretation.summary && (
        <p className="mb-2 text-[10px] leading-relaxed text-white/70">
          {interpretation.summary}
        </p>
      )}
      {interpretation.category_breakdown &&
        interpretation.category_breakdown.length > 0 && (
          <div className="space-y-0.5">
            {interpretation.category_breakdown.map((cat, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px]">
                <span className="min-w-[80px] text-white/70">{cat.category}</span>
                <div className="h-1 flex-1 rounded-full bg-white/20">
                  <div
                    className="h-1 rounded-full bg-white/60"
                    style={{ width: `${Math.min(cat.est_area_pct, 100)}%` }}
                  />
                </div>
                <span className="w-8 text-right font-mono text-white/50">
                  {cat.est_area_pct}%
                </span>
              </div>
            ))}
          </div>
        )}
      {interpretation.tips && interpretation.tips.length > 0 && (
        <div className="mt-2 border-t border-white/10 pt-1.5">
          <span className="font-mono text-[8px] uppercase tracking-wider text-white/40">Tips</span>
          {interpretation.tips.slice(0, 2).map((tip, i) => (
            <p key={i} className="mt-0.5 text-[9px] leading-snug text-white/50">{tip}</p>
          ))}
        </div>
      )}
    </div>
  );
}

interface NaNWarningProps {
  nanPercent: number;
  threshold?: number;
}

/** Warning banner when NaN/nodata percentage is high */
export function NaNWarning({ nanPercent, threshold = 30 }: NaNWarningProps) {
  if (nanPercent < threshold) return null;

  const severity = nanPercent > 60 ? "critical" : nanPercent > 40 ? "high" : "moderate";
  const bgColor =
    severity === "critical"
      ? "bg-red-600/90"
      : severity === "high"
        ? "bg-amber-600/90"
        : "bg-amber-500/80";

  return (
    <div
      className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-white backdrop-blur-sm ${bgColor}`}
    >
      <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
      <span className="text-[10px] font-medium">
        {nanPercent.toFixed(1)}% no-data (clouds/missing)
      </span>
      {severity === "critical" && (
        <span className="text-[9px] text-white/80">- Consider re-running with cloud masking</span>
      )}
    </div>
  );
}

interface StatsSummaryProps {
  statistics: Record<string, unknown>;
  compact?: boolean;
}

/** Compact stats bar for map overlay */
export function StatsSummary({ statistics, compact = true }: StatsSummaryProps) {
  const min = typeof statistics.min === "number" ? statistics.min : null;
  const max = typeof statistics.max === "number" ? statistics.max : null;
  const mean = typeof statistics.mean === "number" ? statistics.mean : null;

  if (min === null && max === null) return null;

  const fmt = (v: number) => (Math.abs(v) < 0.01 ? v.toExponential(2) : v.toFixed(3));

  if (compact) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-white/20 bg-black/75 px-3 py-1.5 font-mono text-[10px] text-white/70 backdrop-blur-sm">
        {min !== null && (
          <span>
            min: <span className="text-white/90">{fmt(min)}</span>
          </span>
        )}
        {mean !== null && (
          <span>
            mean: <span className="text-white/90">{fmt(mean)}</span>
          </span>
        )}
        {max !== null && (
          <span>
            max: <span className="text-white/90">{fmt(max)}</span>
          </span>
        )}
      </div>
    );
  }

  return null;
}

interface ChangeIndicatorProps {
  baselineTitle: string;
  currentTitle: string;
  baselineNaN: number;
  currentNaN: number;
  baselineStats?: Record<string, unknown>;
  currentStats?: Record<string, unknown>;
}

/** Change detection summary comparing baseline vs current */
export function ChangeIndicator({
  baselineTitle,
  currentTitle,
  baselineNaN,
  currentNaN,
  baselineStats,
  currentStats,
}: ChangeIndicatorProps) {
  const nanDelta = currentNaN - baselineNaN;
  const baseMean =
    baselineStats && typeof baselineStats.mean === "number" ? baselineStats.mean : null;
  const currMean =
    currentStats && typeof currentStats.mean === "number" ? currentStats.mean : null;
  const meanDelta = baseMean !== null && currMean !== null ? currMean - baseMean : null;

  return (
    <div className="rounded-lg border border-white/20 bg-black/75 px-3 py-2 backdrop-blur-sm">
      <div className="mb-1.5 flex items-center gap-1">
        <Info className="h-3 w-3 text-white/60" />
        <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-white/90">
          Change Detection
        </span>
      </div>
      <div className="space-y-1 text-[10px]">
        <div className="flex items-center justify-between text-white/70">
          <span>No-data change</span>
          <span className="flex items-center gap-1 font-mono">
            {nanDelta > 0 ? (
              <TrendingUp className="h-3 w-3 text-red-400" />
            ) : (
              <TrendingDown className="h-3 w-3 text-green-400" />
            )}
            <span className={nanDelta > 0 ? "text-red-400" : "text-green-400"}>
              {nanDelta > 0 ? "+" : ""}
              {nanDelta.toFixed(1)}%
            </span>
          </span>
        </div>
        {meanDelta !== null && (
          <div className="flex items-center justify-between text-white/70">
            <span>Mean value shift</span>
            <span className="flex items-center gap-1 font-mono">
              {meanDelta > 0 ? (
                <TrendingUp className="h-3 w-3 text-blue-400" />
              ) : (
                <TrendingDown className="h-3 w-3 text-amber-400" />
              )}
              <span className={meanDelta > 0 ? "text-blue-400" : "text-amber-400"}>
                {meanDelta > 0 ? "+" : ""}
                {meanDelta.toFixed(4)}
              </span>
            </span>
          </div>
        )}
        <div className="mt-1 border-t border-white/10 pt-1 font-mono text-[9px] text-white/40">
          {baselineTitle.slice(0, 30)} vs {currentTitle.slice(0, 30)}
        </div>
      </div>
    </div>
  );
}
