import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Download,
  Eye,
  FileText,
  FolderOpen,
  Layers,
  Loader2,
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
  StatsSummary,
} from "@/components/visualizations/MapOverlay";
import type { SavedJobDetail } from "@/types";

interface ProjectData {
  id: string;
  name: string;
  description: string;
  analysis_count: number;
  created_at: string;
  updated_at: string;
  files: SavedJobDetail[];
}

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

const ProjectDetail: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const { accessToken: authToken } = useAuth();
  const [project, setProject] = useState<ProjectData | null>(null);
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState("Failed to load project.");
  const [selectedAnalysis, setSelectedAnalysis] = useState<SavedJobDetail | null>(null);
  const [downloadingFile, setDownloadingFile] = useState<string | null>(null);

  const fetchProject = useCallback(async () => {
    if (!projectId || !authToken) {
      setErrorMessage(!projectId ? "Missing project ID." : "Authentication required.");
      setViewState("error");
      return;
    }
    setViewState("loading");
    try {
      const res = await fetch(`/api/projects/${projectId}/detail`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!res.ok)
        throw new Error(res.status === 404 ? "Project not found." : "Failed to load project.");
      const data = await res.json();
      setProject(data);
      // Auto-select first analysis
      if (data.files?.length > 0 && !selectedAnalysis) {
        setSelectedAnalysis(data.files[0]!);
      }
      setViewState("data");
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Failed to load project.");
      setViewState("error");
    }
  }, [projectId, authToken]);

  useEffect(() => {
    void fetchProject();
  }, [fetchProject]);

  // Stats summary
  const totalSize = useMemo(
    () => (project?.files || []).reduce((sum, f) => sum + (f.size_bytes || 0), 0),
    [project]
  );

  const handleFileDownload = useCallback(
    async (file: { name: string; download_url?: string }) => {
      if (!authToken || !file.download_url) return;
      setDownloadingFile(file.name);
      try {
        await downloadAuthenticatedFile(file.download_url, file.name, authToken);
      } finally {
        setDownloadingFile(null);
      }
    },
    [authToken],
  );

  if (viewState === "loading") {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <span className="font-mono text-xs text-muted-foreground">Loading project...</span>
        </div>
      </div>
    );
  }

  if (viewState === "error" || !project) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 px-6">
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
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center gap-4 border-b border-border px-6 py-4">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 rounded-lg"
          onClick={() => navigate("/dashboard")}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold">{project.name}</h1>
          <p className="text-xs text-muted-foreground">{project.description || "No description"}</p>
        </div>
        <div className="flex items-center gap-4 font-mono text-[10px] text-muted-foreground">
          <span>{project.files.length} analyses</span>
          <span>{formatBytes(totalSize)}</span>
          <span>{formatDateTime(project.created_at)}</span>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Analysis List */}
        <div className="w-80 flex-shrink-0 overflow-auto border-r border-border bg-card p-4">
          <span className="mb-3 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            <FolderOpen className="mr-1 inline h-3 w-3" />
            Analyses ({project.files.length})
          </span>
          <div className="space-y-2">
            {project.files.map((analysis) => {
              const isSelected = selectedAnalysis?.save_id === analysis.save_id;
              const hasWarnings = (analysis.validation?.warnings?.length || 0) > 0;
              return (
                <button
                  key={analysis.save_id}
                  onClick={() => setSelectedAnalysis(analysis)}
                  className={`block w-full rounded-md border p-3 text-left transition-colors ${
                    isSelected ? "border-primary bg-accent" : "border-border hover:bg-accent/50"
                  }`}
                >
                  <div className="mb-1 flex items-center gap-2">
                    <span className="line-clamp-1 text-sm font-medium">{analysis.title}</span>
                    {hasWarnings && (
                      <ShieldAlert className="h-3 w-3 flex-shrink-0 text-status-warn" />
                    )}
                  </div>
                  <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
                    <span className="rounded bg-primary/10 px-1 py-0.5 text-primary">
                      {analysis.colormap}
                    </span>
                    <span>{formatBytes(analysis.size_bytes)}</span>
                    <span>{analysis.job_files?.length || 0} files</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right: Selected Analysis Detail */}
        <div className="flex-1 overflow-auto p-6">
          {selectedAnalysis ? (
            <div className="space-y-6">
              {/* Title + Actions */}
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold">{selectedAnalysis.title}</h2>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    {selectedAnalysis.save_id} · {formatDateTime(selectedAnalysis.created_at)}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/dashboard/analysis/${selectedAnalysis.save_id}`)}
                  >
                    <Eye className="mr-1.5 h-3.5 w-3.5" />
                    Briefing
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/dashboard/analysis/${selectedAnalysis.save_id}/evidence`)}
                  >
                    <Layers className="mr-1.5 h-3.5 w-3.5" />
                    Evidence
                  </Button>
                </div>
              </div>

              {/* Raster Preview with Overlay */}
              {(selectedAnalysis.result_ext === "tif" ||
                selectedAnalysis.result_ext === "tiff") && (
                <div className="overflow-hidden rounded-lg border">
                  <div className="border-b bg-muted/30 px-4 py-2">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      Map Preview — {selectedAnalysis.colormap}
                    </span>
                  </div>
                  <div className="relative bg-muted/10">
                    <img
                      src={authUrl(`/render-raster?path=${encodeURIComponent(selectedAnalysis.result_path)}&colormap=${encodeURIComponent(selectedAnalysis.colormap)}${selectedAnalysis.vmin != null ? `&vmin=${selectedAnalysis.vmin}` : ""}${selectedAnalysis.vmax != null ? `&vmax=${selectedAnalysis.vmax}` : ""}`, authToken)}
                      alt={selectedAnalysis.title}
                      className="max-h-[400px] w-full object-contain"
                    />
                    {/* Map Overlays */}
                    <div className="absolute bottom-2 left-2 flex flex-col gap-1.5">
                      <IndexLegend
                        index={selectedAnalysis.interpreted_index}
                        colormap={selectedAnalysis.colormap}
                        categoryBreakdown={selectedAnalysis.interpretation?.category_breakdown}
                      />
                      {selectedAnalysis.statistics && (
                        <StatsSummary statistics={selectedAnalysis.statistics} />
                      )}
                    </div>
                    <div className="absolute right-2 top-2 flex flex-col gap-1.5">
                      {selectedAnalysis.interpretation && (
                        <InterpretationBadge interpretation={selectedAnalysis.interpretation} compact />
                      )}
                    </div>
                    <div className="absolute left-1/2 top-2 -translate-x-1/2">
                      {selectedAnalysis.statistics &&
                        typeof selectedAnalysis.statistics.nan_percent === "number" && (
                          <NaNWarning nanPercent={selectedAnalysis.statistics.nan_percent as number} />
                        )}
                    </div>
                  </div>
                </div>
              )}

              {/* Metadata Grid */}
              <div className="grid grid-cols-3 gap-4">
                <MetaCard label="Colormap" value={selectedAnalysis.colormap} />
                <MetaCard label="File Size" value={formatBytes(selectedAnalysis.size_bytes)} />
                <MetaCard
                  label="Bounds"
                  value={
                    selectedAnalysis.bounds
                      ? selectedAnalysis.bounds
                          .flat()
                          .map((v) => Number(v).toFixed(3))
                          .join(", ")
                      : "N/A"
                  }
                />
                {selectedAnalysis.vmin != null && (
                  <MetaCard label="Value Min" value={selectedAnalysis.vmin.toFixed(4)} />
                )}
                {selectedAnalysis.vmax != null && (
                  <MetaCard label="Value Max" value={selectedAnalysis.vmax.toFixed(4)} />
                )}
                {selectedAnalysis.source_query && (
                  <MetaCard label="Source Query" value={selectedAnalysis.source_query} />
                )}
              </div>

              {/* Statistics */}
              {selectedAnalysis.statistics && typeof selectedAnalysis.statistics === "object" && (
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    <MapPinned className="mr-1 inline h-3 w-3" />
                    Statistics
                  </h3>
                  <div className="grid grid-cols-4 gap-3">
                    {Object.entries(selectedAnalysis.statistics as Record<string, unknown>)
                      .filter(([, v]) => typeof v === "number" || typeof v === "string")
                      .slice(0, 8)
                      .map(([k, v]) => (
                        <MetaCard
                          key={k}
                          label={k.replace(/_/g, " ")}
                          value={
                            typeof v === "number"
                              ? Number.isInteger(v)
                                ? String(v)
                                : Number(v).toFixed(4)
                              : String(v)
                          }
                        />
                      ))}
                  </div>
                </div>
              )}

              {/* AI Interpretation (Push Mode) */}
              {selectedAnalysis.interpretation && (
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    AI Interpretation
                    {selectedAnalysis.interpreted_index && (
                      <span className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 text-primary">
                        {selectedAnalysis.interpreted_index}
                      </span>
                    )}
                  </h3>
                  <div className="rounded-lg border bg-card p-4">
                    <div className="flex items-center gap-3">
                      <span
                        className={`flex h-12 w-12 items-center justify-center rounded-lg text-lg font-bold ${
                          selectedAnalysis.interpretation.grade === "A" ||
                          selectedAnalysis.interpretation.grade === "B"
                            ? "bg-status-good/10 text-status-good"
                            : selectedAnalysis.interpretation.grade === "C"
                              ? "bg-status-warn/10 text-status-warn"
                              : "bg-status-critical/10 text-status-critical"
                        }`}
                      >
                        {selectedAnalysis.interpretation.grade || "?"}
                      </span>
                      <div>
                        <div className="text-sm font-semibold">
                          {selectedAnalysis.interpretation.mean_category || "Analysis Result"}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          Score: {selectedAnalysis.interpretation.grade_score ?? "N/A"}/100
                          {selectedAnalysis.interpretation.domain &&
                            ` | Domain: ${selectedAnalysis.interpretation.domain}`}
                        </div>
                      </div>
                    </div>
                    {selectedAnalysis.interpretation.summary && (
                      <p className="mt-3 text-sm leading-relaxed">
                        {selectedAnalysis.interpretation.summary}
                      </p>
                    )}
                    {selectedAnalysis.interpretation.category_breakdown &&
                      selectedAnalysis.interpretation.category_breakdown.length > 0 && (
                        <div className="mt-3 space-y-1">
                          {selectedAnalysis.interpretation.category_breakdown.map((cat, i) => (
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
                    {selectedAnalysis.interpretation.tips &&
                      selectedAnalysis.interpretation.tips.length > 0 && (
                        <div className="mt-3 border-t pt-3">
                          <span className="font-mono text-[10px] uppercase text-muted-foreground">
                            Recommendations
                          </span>
                          <ul className="mt-1 space-y-1">
                            {selectedAnalysis.interpretation.tips.map((tip, i) => (
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

              {/* Validation */}
              {selectedAnalysis.validation && (
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    <ShieldAlert className="mr-1 inline h-3 w-3" />
                    Validation
                  </h3>
                  <div
                    className={`rounded-md border p-3 ${
                      selectedAnalysis.validation.is_valid
                        ? "border-status-good/30 bg-status-good/5"
                        : "border-status-critical/30 bg-status-critical/5"
                    }`}
                  >
                    <span
                      className={`font-mono text-xs font-medium ${
                        selectedAnalysis.validation.is_valid
                          ? "text-status-good"
                          : "text-status-critical"
                      }`}
                    >
                      {selectedAnalysis.validation.is_valid ? "Valid" : "Invalid"}
                    </span>
                    {(selectedAnalysis.validation.warnings || []).length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {selectedAnalysis.validation.warnings.map((w: string, i: number) => (
                          <li key={i} className="text-xs text-status-warn">
                            {w}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )}

              {/* Archive Files */}
              {selectedAnalysis.job_files && selectedAnalysis.job_files.length > 0 && (
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    <FileText className="mr-1 inline h-3 w-3" />
                    Archive Files ({selectedAnalysis.job_files.length})
                  </h3>
                  <div className="space-y-2">
                    {selectedAnalysis.job_files.map((f) => (
                      <div
                        key={f.name}
                        className="flex items-center justify-between rounded-md border bg-card px-4 py-3"
                      >
                        <div>
                          <span className="text-sm font-medium">{f.name}</span>
                          <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                            {f.ext?.toUpperCase()} · {formatBytes(f.size_bytes)}
                          </span>
                        </div>
                        {f.download_url && authToken && (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={downloadingFile === f.name}
                            onClick={() => void handleFileDownload(f)}
                          >
                            <Download className="mr-1 h-3.5 w-3.5" />
                            {downloadingFile === f.name ? "Downloading..." : "Download"}
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Raw Metadata */}
              {selectedAnalysis.metadata && (
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Raw Metadata
                  </h3>
                  <pre className="max-h-48 overflow-auto rounded-md bg-muted/30 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
                    {JSON.stringify(selectedAnalysis.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <div className="flex h-full items-center justify-center">
              <div className="text-center">
                <Layers className="mx-auto mb-3 h-10 w-10 text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">
                  Select an analysis from the list to view its details, map preview, and files.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/30 p-3">
      <span className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="mt-1 block truncate text-sm font-medium">{value}</span>
    </div>
  );
}

export default ProjectDetail;
