import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ChevronRight,
  ChevronLeft,
  Download,
  Layers,
  Info,
  MapPinned,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/auth/useAuth";
import { formatBytes, formatDateTime, authUrl } from "@/lib/utils";
import {
  IndexLegend,
  InterpretationBadge,
  NaNWarning,
  StatsSummary,
} from "@/components/visualizations/MapOverlay";
import type { SavedJobDetail } from "@/types";

type ViewState = "loading" | "data" | "error";

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

const EvidenceExplorer: React.FC = () => {
  const { analysisId, assetId } = useParams<{ analysisId?: string; assetId?: string }>();
  const savedAnalysisId = analysisId ?? assetId;
  const navigate = useNavigate();
  const { accessToken: authToken } = useAuth();
  const [detail, setDetail] = useState<SavedJobDetail | null>(null);
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState("Failed to load evidence.");
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [activeColormap, setActiveColormap] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  // Available colormaps for layer switching
  const COLORMAPS = [
    { id: "viridis", label: "Viridis" },
    { id: "RdYlGn", label: "Red-Yellow-Green" },
    { id: "Blues", label: "Blues" },
    { id: "hot", label: "Thermal" },
    { id: "terrain", label: "Terrain" },
    { id: "RdBu_r", label: "Diverging" },
  ];

  const fetchDetail = useCallback(async () => {
    if (!savedAnalysisId || !authToken) {
      setErrorMessage(!savedAnalysisId ? "Missing analysis id." : "Authentication required.");
      setViewState("error");
      return;
    }

    setViewState("loading");
    try {
      const response = await fetch(`/saved-jobs/${savedAnalysisId}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });

      if (!response.ok) {
        throw new Error(
          response.status === 404 ? "Saved analysis not found." : "Failed to load evidence.",
        );
      }

      const data = (await response.json()) as SavedJobDetail;
      setDetail(data);
      setActiveColormap(data.colormap || "viridis");
      setViewState("data");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load evidence.");
      setViewState("error");
    }
  }, [savedAnalysisId, authToken]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  // Build raster tile URL from saved result
  const rasterUrl = useMemo(() => {
    if (!detail) return null;
    const ext = detail.result_ext || "tif";
    if (ext !== "tif" && ext !== "tiff") return null;
    const cmap = activeColormap || detail.colormap || "viridis";
    const params = new URLSearchParams({ colormap: cmap });
    if (detail.vmin != null) params.set("vmin", String(detail.vmin));
    if (detail.vmax != null) params.set("vmax", String(detail.vmax));
    const path = encodeURIComponent(detail.result_path);
    return authUrl(`/render-raster?path=${path}&${params.toString()}`, authToken);
  }, [detail, activeColormap, authToken]);

  // Extract statistics for the info panel
  const stats = useMemo(() => {
    if (!detail?.statistics || typeof detail.statistics !== "object") return [];
    return Object.entries(detail.statistics)
      .filter(([, v]) => typeof v === "number" || typeof v === "string")
      .slice(0, 12)
      .map(([k, v]) => ({
        label: k.replace(/_/g, " "),
        value: typeof v === "number" ? (Number.isInteger(v) ? String(v) : Number(v).toFixed(4)) : String(v),
      }));
  }, [detail]);

  // Bounds for display
  const boundsText = useMemo(() => {
    if (!detail?.bounds || detail.bounds.length === 0) return null;
    const flat = detail.bounds.flat();
    return flat.map((v) => Number(v).toFixed(4)).join(", ");
  }, [detail]);

  // Validation warnings
  const warnings = detail?.validation?.warnings || [];

  const handleDownload = useCallback(async () => {
    if (!authToken || !detail?.download_url) return;
    setDownloading(true);
    try {
      const filename = detail.result_filename || `${detail.save_id}.${detail.result_ext || "dat"}`;
      await downloadAuthenticatedFile(detail.download_url, filename, authToken);
    } finally {
      setDownloading(false);
    }
  }, [authToken, detail]);

  if (viewState === "loading") {
    return (
      <div className="flex h-full items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
          <span className="font-mono text-xs text-muted-foreground">Loading evidence...</span>
        </div>
      </div>
    );
  }

  if (viewState === "error" || !detail) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-background px-6">
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">
          <p className="mb-3 text-sm font-medium text-destructive">{errorMessage}</p>
          <Button variant="outline" size="sm" onClick={() => navigate("/dashboard")}>
            Back to Dashboard
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-full flex-col">
      {/* Header */}
      <div className="z-10 flex items-center gap-4 border-b border-border bg-background px-6 py-3">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 rounded-lg"
          onClick={() => navigate(`/dashboard/analysis/${savedAnalysisId}`)}
          aria-label="Back to briefing"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold">{detail.title}</h1>
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Evidence Explorer
          </span>
        </div>
        <div className="flex items-center gap-2">
          {detail.project && (
            <span className="rounded-md border border-border px-2 py-1 font-mono text-[10px] text-muted-foreground">
              {detail.project.name}
            </span>
          )}
          <span className="font-mono text-[10px] text-muted-foreground">
            {formatDateTime(detail.created_at)}
          </span>
        </div>
      </div>

      {/* Main Content */}
      <div className="relative flex-1 overflow-hidden">
        {/* Raster Display */}
        {rasterUrl ? (
          <div className="relative h-full w-full bg-muted">
            <img
              src={rasterUrl}
              alt={detail.title}
              className="h-full w-full object-contain"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            {/* Map Overlays */}
            <div className="absolute bottom-3 left-3 z-[1002] flex flex-col gap-1.5">
              <IndexLegend
                index={detail.interpreted_index}
                colormap={activeColormap || detail.colormap || "viridis"}
                categoryBreakdown={detail.interpretation?.category_breakdown}
              />
              {detail.statistics && (
                <StatsSummary statistics={detail.statistics} />
              )}
            </div>
            <div className="absolute right-3 top-14 z-[1002] flex flex-col gap-1.5">
              {detail.interpretation && (
                <InterpretationBadge interpretation={detail.interpretation} compact />
              )}
            </div>
            <div className="absolute left-1/2 top-14 z-[1002] -translate-x-1/2">
              {detail.statistics &&
                typeof detail.statistics.nan_percent === "number" && (
                  <NaNWarning nanPercent={detail.statistics.nan_percent as number} />
                )}
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center bg-muted">
            <div className="text-center">
              <Layers className="mx-auto mb-2 h-10 w-10 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                {detail.result_ext === "tif" || detail.result_ext === "tiff"
                  ? "Unable to render raster preview"
                  : `File type .${detail.result_ext || "unknown"} — preview not available`}
              </p>
              {detail.download_url && authToken && (
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  disabled={downloading}
                  onClick={() => void handleDownload()}
                >
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  {downloading ? "Downloading..." : "Download File"}
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Telemetry Bar */}
        <div className="absolute left-0 right-0 top-0 z-[1001] flex h-10 items-center border-b border-border bg-background/80 px-6 font-mono text-[10px] backdrop-blur">
          <div className="flex items-center gap-6">
            {boundsText && (
              <div className="flex gap-2">
                <span className="uppercase text-muted-foreground">BOUNDS:</span>
                <span className="text-status-good">{boundsText}</span>
              </div>
            )}
            <div className="flex gap-2">
              <span className="uppercase text-muted-foreground">COLORMAP:</span>
              <span>{activeColormap?.toUpperCase()}</span>
            </div>
            <div className="flex gap-2">
              <span className="uppercase text-muted-foreground">SIZE:</span>
              <span>{formatBytes(detail.size_bytes)}</span>
            </div>
            {detail.source_query && (
              <div className="flex gap-2">
                <span className="uppercase text-muted-foreground">QUERY:</span>
                <span className="max-w-[200px] truncate">{detail.source_query}</span>
              </div>
            )}
          </div>
          <div className="ml-auto flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <div className="h-1.5 w-1.5 rounded-full bg-green-500" />
              <span className="text-green-600 dark:text-green-400">ARCHIVED</span>
            </div>
          </div>
        </div>

        {/* Technical Panel */}
        <div
          className={`absolute right-4 top-14 z-[1001] max-h-[calc(100%-7rem)] overflow-auto border border-border bg-background transition-all ${
            panelCollapsed ? "w-10" : "w-80"
          }`}
          style={{ boxShadow: "2px 2px 0 rgba(0,0,0,0.06)" }}
        >
          {panelCollapsed ? (
            <button
              onClick={() => setPanelCollapsed(false)}
              className="flex h-full w-full items-center justify-center py-4"
              aria-label="Expand panel"
            >
              <ChevronLeft className="h-4 w-4 text-muted-foreground" />
            </button>
          ) : (
            <>
              {/* Panel header */}
              <div className="flex items-center justify-between border-b border-border bg-card px-4 py-3">
                <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Evidence Panel
                </span>
                <button
                  onClick={() => setPanelCollapsed(true)}
                  className="text-muted-foreground transition-colors hover:text-foreground"
                  aria-label="Collapse panel"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-5 p-4">
                {/* Colormap Selector */}
                <div>
                  <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    Colormap
                  </span>
                  <div className="space-y-1.5">
                    {COLORMAPS.map((cmap) => (
                      <label
                        key={cmap.id}
                        className="flex cursor-pointer items-center gap-2 text-sm transition-colors hover:text-foreground"
                      >
                        <input
                          type="radio"
                          name="colormap"
                          checked={activeColormap === cmap.id}
                          onChange={() => setActiveColormap(cmap.id)}
                          className="accent-primary"
                        />
                        {cmap.label}
                      </label>
                    ))}
                  </div>
                </div>

                {/* Value Range */}
                {(detail.vmin != null || detail.vmax != null) && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      Value Range
                    </span>
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-sm">
                      <span className="text-muted-foreground">Min</span>
                      <span className="text-right">{detail.vmin?.toFixed(4) ?? "auto"}</span>
                      <span className="text-muted-foreground">Max</span>
                      <span className="text-right">{detail.vmax?.toFixed(4) ?? "auto"}</span>
                    </div>
                  </div>
                )}

                {/* Statistics */}
                {stats.length > 0 && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      <Info className="mr-1 inline h-3 w-3" />
                      Statistics
                    </span>
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-sm">
                      {stats.map((s) => (
                        <React.Fragment key={s.label}>
                          <span className="text-muted-foreground">{s.label}</span>
                          <span className="text-right">{s.value}</span>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                )}

                {/* Validation */}
                {warnings.length > 0 && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-status-warn">
                      Validation Warnings
                    </span>
                    <ul className="space-y-1">
                      {warnings.map((w, i) => (
                        <li key={i} className="text-xs text-status-warn">
                          {w}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Result Files */}
                {detail.job_files && detail.job_files.length > 0 && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      Result Files
                    </span>
                    <div className="space-y-1">
                      {detail.job_files.map((f) => (
                        <div
                          key={f.name}
                          className="flex items-center justify-between rounded-md bg-muted/50 px-2 py-1.5 text-xs"
                        >
                          <span className="truncate font-mono">{f.name}</span>
                          <span className="ml-2 shrink-0 text-muted-foreground">
                            {formatBytes(f.size_bytes)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Bounds */}
                {boundsText && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      <MapPinned className="mr-1 inline h-3 w-3" />
                      Spatial Extent
                    </span>
                    <p className="break-all font-mono text-xs text-muted-foreground">{boundsText}</p>
                  </div>
                )}

                {/* Source Query */}
                {detail.source_query && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      Source Query
                    </span>
                    <p className="rounded-md bg-muted/50 p-2 text-xs leading-relaxed">
                      {detail.source_query}
                    </p>
                  </div>
                )}

                {/* Download */}
                {detail.download_url && authToken && (
                  <Button
                    variant="outline"
                    className="w-full gap-2 font-mono text-[10px] uppercase tracking-widest"
                    disabled={downloading}
                    onClick={() => void handleDownload()}
                  >
                    <Download className="h-3.5 w-3.5" />
                    {downloading ? "Downloading..." : "Download Result"}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default EvidenceExplorer;
