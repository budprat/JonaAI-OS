import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Database,
  Download,
  FolderOpen,
  HardDrive,
  MapPinned,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/auth/useAuth";
import { formatBytes, formatDateTime, authUrl } from "@/lib/utils";
import {
  IndexLegend,
  InterpretationBadge,
  NaNWarning,
} from "@/components/visualizations/MapOverlay";
import type { JobFile, SavedJobDetail } from "@/types";

type ViewState = "loading" | "data" | "error";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatStatValue(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toString() : value.toFixed(4);
  }
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (value == null) {
    return "N/A";
  }
  return String(value);
}

function buildStatGroups(
  statistics: SavedJobDetail["statistics"],
): Array<{ title: string; rows: Array<{ label: string; value: string }> }> {
  if (!isPlainObject(statistics)) {
    return [];
  }

  const topLevelRows = Object.entries(statistics)
    .filter(([, value]) => !isPlainObject(value))
    .map(([label, value]) => ({ label, value: formatStatValue(value) }));

  const nestedGroups = Object.entries(statistics)
    .filter(([, value]) => isPlainObject(value))
    .map(([title, value]) => ({
      title,
      rows: Object.entries(value as Record<string, unknown>).map(([label, nestedValue]) => ({
        label,
        value: formatStatValue(nestedValue),
      })),
    }))
    .filter((group) => group.rows.length > 0);

  const groups = [...nestedGroups];
  if (topLevelRows.length > 0) {
    groups.unshift({ title: "summary", rows: topLevelRows });
  }
  return groups;
}

function formatBounds(bounds?: number[][]): string {
  if (!bounds || bounds.length === 0) {
    return "No bounds recorded";
  }

  const flattened = bounds.flat();
  if (flattened.length === 4) {
    return flattened.map((value) => Number(value).toFixed(4)).join(", ");
  }

  return flattened.map((value) => Number(value).toFixed(4)).join(", ");
}

async function downloadAuthenticatedFile(
  url: string,
  filename: string,
  authToken: string,
): Promise<void> {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${authToken}` },
  });

  if (!response.ok) {
    throw new Error(`Download failed with status ${response.status}`);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

const AssetBriefing: React.FC = () => {
  const { analysisId, assetId } = useParams<{ analysisId?: string; assetId?: string }>();
  const savedAnalysisId = analysisId ?? assetId;
  const navigate = useNavigate();
  const { accessToken: authToken } = useAuth();
  const [detail, setDetail] = useState<SavedJobDetail | null>(null);
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState("Failed to load saved analysis details.");
  const [downloading, setDownloading] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    if (!savedAnalysisId) {
      setErrorMessage("Missing saved analysis id.");
      setViewState("error");
      return;
    }

    if (!authToken) {
      setErrorMessage("Geo Hub detail pages require an authenticated session.");
      setViewState("error");
      return;
    }

    setViewState("loading");
    setErrorMessage("Failed to load saved analysis details.");

    try {
      const response = await fetch(`/saved-jobs/${savedAnalysisId}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });

      if (!response.ok) {
        throw new Error(
          response.status === 404 ? "Saved analysis not found." : "Failed to load saved analysis.",
        );
      }

      const nextDetail = (await response.json()) as SavedJobDetail;
      setDetail(nextDetail);
      setViewState("data");
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Failed to load saved analysis details.",
      );
      setViewState("error");
    }
  }, [savedAnalysisId, authToken]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const previewUrl = useMemo(() => {
    if (!detail) return null;
    if (detail.result_ext !== "tif" && detail.result_ext !== "tiff") {
      return null;
    }
    const params = new URLSearchParams({
      path: detail.result_path,
      colormap: detail.colormap || "viridis",
    });
    if (detail.vmin != null) params.set("vmin", String(detail.vmin));
    if (detail.vmax != null) params.set("vmax", String(detail.vmax));
    return authUrl(`/render-raster?${params.toString()}`, authToken);
  }, [detail, authToken]);

  const statGroups = useMemo(() => buildStatGroups(detail?.statistics), [detail?.statistics]);
  const validationWarnings = detail?.validation?.warnings || [];
  const hasWarnings = validationWarnings.length > 0;
  const projectLabel = detail?.project?.name || "Unassigned";

  const handleDownload = useCallback(
    async (file: Pick<JobFile, "name"> & { download_url?: string }) => {
      if (!authToken || !file.download_url) {
        return;
      }

      setDownloading(file.name);
      try {
        await downloadAuthenticatedFile(file.download_url, file.name, authToken);
      } finally {
        setDownloading(null);
      }
    },
    [authToken],
  );

  if (viewState === "loading") {
    return (
      <div className="screen-enter flex h-full flex-col">
        <div className="border-b border-border px-6 py-4">
          <div className="h-4 w-32 animate-pulse rounded bg-muted" />
          <div className="mt-3 h-8 w-72 animate-pulse rounded bg-muted" />
        </div>
        <div className="grid flex-1 grid-cols-10 gap-0">
          {[0, 1, 2].map((column) => (
            <div key={column} className="border-r border-border p-6 last:border-r-0">
              <div className="h-4 w-28 animate-pulse rounded bg-muted" />
              <div className="mt-4 space-y-3">
                {[0, 1, 2, 3].map((item) => (
                  <div key={item} className="h-20 animate-pulse rounded border bg-muted/50" />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (viewState === "error" || !detail) {
    return (
      <div className="screen-enter flex h-full flex-col">
        <div className="flex items-center gap-4 border-b border-border px-6 py-4">
          <button
            onClick={() => navigate("/dashboard")}
            className="rounded-md p-1.5 transition-colors hover:bg-accent"
            aria-label="Go back"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <h1 className="text-2xl font-semibold">Saved Analysis Briefing</h1>
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Archive detail
            </span>
          </div>
        </div>

        <div className="flex flex-1 items-center justify-center p-6">
          <div className="w-full max-w-lg rounded-lg border bg-card p-6 text-center">
            <ShieldAlert className="mx-auto h-10 w-10 text-amber-500" aria-hidden="true" />
            <h2 className="mt-4 text-lg font-semibold">Detail page unavailable</h2>
            <p className="mt-2 text-sm text-muted-foreground">{errorMessage}</p>
            <div className="mt-4 flex items-center justify-center gap-3">
              <Button variant="outline" onClick={() => navigate("/dashboard")}>
                Return to Portfolio
              </Button>
              <Button onClick={() => void fetchDetail()}>Retry</Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="screen-enter flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-border px-6 py-4">
        <button
          onClick={() => navigate("/dashboard")}
          className="rounded-md p-1.5 transition-colors hover:bg-accent"
          aria-label="Go back"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>

        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Save ID {detail.save_id}
            </span>
            <span className="rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {projectLabel}
            </span>
            <span
              className={`rounded-md px-2 py-0.5 font-mono text-[10px] font-bold ${
                detail.validation?.is_valid === false || hasWarnings
                  ? "bg-status-warn/10 text-status-warn"
                  : "bg-status-good/10 text-status-good"
              }`}
            >
              {detail.validation?.is_valid === false || hasWarnings ? "Warnings present" : "Archive healthy"}
            </span>
          </div>
          <h1 className="truncate text-2xl font-semibold">{detail.title}</h1>
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            {detail.result_ext?.toUpperCase() || "RESULT"} · {formatDateTime(detail.created_at)}
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {detail.download_url && detail.result_filename && authToken && (
            <Button
              variant="outline"
              size="sm"
              disabled={downloading === detail.result_filename}
              onClick={() =>
                void handleDownload({
                  name: detail.result_filename || `${detail.save_id}.${detail.result_ext || "dat"}`,
                  download_url: detail.download_url,
                })
              }
            >
              <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {downloading === detail.result_filename ? "Downloading..." : "Download"}
            </Button>
          )}

          <Button
            variant="default"
            size="sm"
            onClick={() => navigate(`/dashboard/analysis/${detail.save_id}/evidence`)}
          >
            Open Evidence Explorer (Preview)
            <ArrowRight className="ml-1.5 h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div className="grid flex-1 grid-cols-10 overflow-hidden">
        <div className="col-span-3 overflow-auto border-r border-border p-6">
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Archive Summary
          </span>

          <div className="mt-4 grid gap-3">
            <SummaryCard
              icon={<FolderOpen className="h-4 w-4 text-primary" aria-hidden="true" />}
              label="Project"
              value={projectLabel}
              detail={detail.project?.description || "Saved result archive assignment"}
            />
            <SummaryCard
              icon={<HardDrive className="h-4 w-4 text-primary" aria-hidden="true" />}
              label="Stored Size"
              value={formatBytes(detail.size_bytes)}
              detail={detail.result_filename || detail.result_path}
            />
            <SummaryCard
              icon={<Database className="h-4 w-4 text-primary" aria-hidden="true" />}
              label="Created"
              value={formatDateTime(detail.created_at)}
              detail={detail.job_id ? `OpenEO job ${detail.job_id}` : "No upstream batch id recorded"}
            />
            <SummaryCard
              icon={<MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />}
              label="Bounds"
              value={formatBounds(detail.bounds)}
              detail="Recorded spatial extent for the archived output"
            />
            <SummaryCard
              icon={<Activity className="h-4 w-4 text-primary" aria-hidden="true" />}
              label="Earth Intelligence"
              value={detail.earth_intelligence?.asset?.name || "Standalone analysis"}
              detail={
                detail.earth_intelligence?.observation
                  ? `Observation ${detail.earth_intelligence.observation.status}`
                  : `${detail.earth_intelligence?.evidence_count ?? 0} evidence packages linked`
              }
            />
          </div>

          <div className="mt-8">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Source Query
            </span>
            <div className="mt-3 rounded-lg border bg-card p-4">
              <p className="text-sm leading-relaxed text-muted-foreground">
                {detail.source_query || "No source query was stored for this archive entry."}
              </p>
            </div>
          </div>

          <div className="mt-8">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Validation Findings
            </span>
            <div className="mt-3 space-y-3">
              {validationWarnings.length > 0 ? (
                validationWarnings.map((warning, index) => (
                  <div
                    key={`${warning}-${index}`}
                    className="rounded-lg border border-status-warn/30 bg-status-warn/5 p-4"
                  >
                    <div className="font-mono text-[10px] uppercase tracking-wider text-status-warn">
                      Warning {index + 1}
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">{warning}</p>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border bg-card p-4">
                  <div className="font-mono text-[10px] uppercase tracking-wider text-status-good">
                    Validation
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {detail.validation?.is_valid === false
                      ? "The archive flagged issues, but no warning details were persisted."
                      : "No validation warnings were recorded for this result."}
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* AI Interpretation */}
          {detail.interpretation && (
            <div className="mt-8">
              <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                AI Interpretation
                {detail.interpreted_index && (
                  <span className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 text-primary">
                    {detail.interpreted_index}
                  </span>
                )}
              </span>
              <div className="mt-3 rounded-lg border bg-card p-4">
                <div className="flex items-center gap-3">
                  <span
                    className={`flex h-12 w-12 items-center justify-center rounded-lg text-lg font-bold ${
                      detail.interpretation.grade === "A" ||
                      detail.interpretation.grade === "B"
                        ? "bg-status-good/10 text-status-good"
                        : detail.interpretation.grade === "C"
                          ? "bg-status-warn/10 text-status-warn"
                          : "bg-status-critical/10 text-status-critical"
                    }`}
                  >
                    {detail.interpretation.grade || "?"}
                  </span>
                  <div>
                    <div className="text-sm font-semibold">
                      {detail.interpretation.mean_category || "Analysis Result"}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      Score: {detail.interpretation.grade_score ?? "N/A"}/100
                      {detail.interpretation.domain &&
                        ` | Domain: ${detail.interpretation.domain}`}
                    </div>
                  </div>
                </div>
                {detail.interpretation.summary && (
                  <p className="mt-3 text-sm leading-relaxed">
                    {detail.interpretation.summary}
                  </p>
                )}
                {detail.interpretation.category_breakdown &&
                  detail.interpretation.category_breakdown.length > 0 && (
                    <div className="mt-3 space-y-1">
                      {detail.interpretation.category_breakdown.map((cat, i) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span>{cat.category}</span>
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-20 rounded-full bg-muted">
                              <div
                                className="h-1.5 rounded-full bg-primary"
                                style={{ width: `${Math.min(cat.est_area_pct, 100)}%` }}
                              />
                            </div>
                            <span className="font-mono text-muted-foreground">
                              {cat.est_area_pct}%
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                {detail.interpretation.tips &&
                  detail.interpretation.tips.length > 0 && (
                    <div className="mt-3 border-t pt-3">
                      <span className="font-mono text-[10px] uppercase text-muted-foreground">
                        Recommendations
                      </span>
                      <ul className="mt-1 space-y-1">
                        {detail.interpretation.tips.map((tip, i) => (
                          <li key={i} className="text-xs text-muted-foreground">
                            {tip}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
              </div>
            </div>
          )}

          {/* Quality Assessment */}
          {detail.quality_assessment && (
            <div className="mt-8">
              <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                Quality Assessment
              </span>
              <div className="mt-3 rounded-lg border bg-card p-4">
                <div className="flex items-center gap-3">
                  <span
                    className={`flex h-10 w-10 items-center justify-center rounded-lg text-sm font-bold ${
                      detail.quality_assessment.grade === "A" ||
                      detail.quality_assessment.grade === "B"
                        ? "bg-status-good/10 text-status-good"
                        : detail.quality_assessment.grade === "C"
                          ? "bg-status-warn/10 text-status-warn"
                          : "bg-status-critical/10 text-status-critical"
                    }`}
                  >
                    {detail.quality_assessment.grade || "?"}
                  </span>
                  <div>
                    <div className="text-sm font-semibold">
                      Quality Grade: {detail.quality_assessment.grade || "N/A"}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      Score: {detail.quality_assessment.score ?? "N/A"}/100
                    </div>
                  </div>
                </div>
                {detail.quality_assessment.summary && (
                  <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                    {detail.quality_assessment.summary}
                  </p>
                )}
                {detail.quality_assessment.flags &&
                  detail.quality_assessment.flags.length > 0 && (
                    <div className="mt-3 border-t pt-3">
                      <span className="font-mono text-[10px] uppercase text-muted-foreground">
                        Flags
                      </span>
                      <ul className="mt-1 space-y-1">
                        {detail.quality_assessment.flags.map((flag, i) => (
                          <li key={i} className="text-xs text-status-warn">
                            {flag}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
              </div>
            </div>
          )}
        </div>

        <div className="col-span-3 overflow-auto border-r border-border p-6">
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Result Metadata
          </span>

          <div className="mt-4 space-y-4">
            {statGroups.length > 0 ? (
              statGroups.map((group) => (
                <div key={group.title} className="rounded-lg border bg-card p-4">
                  <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    {group.title}
                  </div>
                  <div className="mt-3 space-y-2">
                    {group.rows.map((row) => (
                      <div key={row.label} className="flex items-start justify-between gap-3 text-sm">
                        <span className="font-mono text-xs uppercase text-muted-foreground">
                          {row.label}
                        </span>
                        <span className="text-right font-medium">{row.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="rounded-lg border bg-card p-4">
                <p className="text-sm text-muted-foreground">
                  No structured statistics were stored for this saved result.
                </p>
              </div>
            )}
          </div>

          <div className="mt-8">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Archived Files
            </span>
            <div className="mt-3 space-y-3">
              {(detail.job_files || []).length > 0 ? (
                detail.job_files?.map((file) => (
                  <div key={file.name} className="rounded-lg border bg-card p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{file.name}</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {file.ext.toUpperCase()} · {formatBytes(file.size_bytes)} · {formatDateTime(file.modified)}
                        </div>
                      </div>
                      {file.download_url && authToken && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={downloading === file.name}
                          onClick={() => void handleDownload(file)}
                        >
                          <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                          {downloading === file.name ? "Downloading..." : "Get"}
                        </Button>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border bg-card p-4">
                  <p className="text-sm text-muted-foreground">
                    No auxiliary archive files were indexed for this saved result.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="col-span-4 overflow-auto bg-card">
          <div className="border-b border-border p-4">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Saved Output Preview
            </span>
          </div>

          <div className="p-6">
            {previewUrl ? (
              <div className="overflow-hidden rounded-lg border bg-background">
                <div className="border-b border-border px-4 py-3">
                  <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    Rendered from archived raster
                  </div>
                </div>
                <div className="relative bg-muted/20 p-4">
                  <img
                    src={previewUrl}
                    alt={`Preview of ${detail.title}`}
                    className="max-h-[28rem] w-full rounded-md object-contain"
                  />
                  {/* Map Overlays */}
                  <div className="absolute bottom-2 left-2 flex flex-col gap-1.5">
                    <IndexLegend
                      index={detail.interpreted_index}
                      colormap={detail.colormap || "viridis"}
                      categoryBreakdown={detail.interpretation?.category_breakdown}
                    />
                  </div>
                  <div className="absolute right-2 top-2 flex flex-col gap-1.5">
                    {detail.interpretation && (
                      <InterpretationBadge interpretation={detail.interpretation} compact />
                    )}
                  </div>
                  <div className="absolute left-1/2 top-2 -translate-x-1/2">
                    {detail.statistics &&
                      typeof detail.statistics.nan_percent === "number" && (
                        <NaNWarning nanPercent={detail.statistics.nan_percent as number} />
                      )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-lg border bg-background p-6">
                <h2 className="text-sm font-semibold">Preview unavailable</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  This archive entry does not have a GeoTIFF preview path yet. Use the file list to
                  inspect or download the saved output.
                </p>
              </div>
            )}

            <div className="mt-6 rounded-lg border bg-background p-4">
              <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                Raw Metadata Snapshot
              </div>
              <pre className="mt-3 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
                {JSON.stringify(detail.metadata || {}, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

function SummaryCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2">
        {icon}
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      </div>
      <div className="mt-3 text-sm font-medium">{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{detail}</div>
    </div>
  );
}

export default AssetBriefing;
