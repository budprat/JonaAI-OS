import { memo, useState } from "react";
import {
  Database,
  Layers,
  MapPin,
  GitBranch,
  Play,
  ListChecks,
  Clock,
  Download,
  BarChart3,
  Archive,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Globe,
  FileText,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn, formatBytes, formatDate } from "@/lib/utils";

// --- Tool result type definitions ---

/** Narrowing helper: validates that a value is a non-null object. */
function isRecord(v: unknown): v is Record<string, unknown> {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

interface CollectionItem {
  id?: string;
  title?: string;
  description?: string;
}

interface CollectionInfoResult {
  id?: string;
  title?: string;
  bands?: Record<string, unknown>;
  extent?: {
    temporal?: { interval?: [string | null, string | null][] };
  };
}

interface LocationResult {
  bbox?: Record<string, number>;
  spatial_extent?: Record<string, number>;
  location?: string;
  name?: string;
  west?: number;
  south?: number;
  east?: number;
  north?: number;
  W?: number;
  S?: number;
  E?: number;
  N?: number;
}

interface ProcessGraphNode {
  process_id?: string;
  [key: string]: unknown;
}

interface JobResult {
  id?: string;
  status?: string;
  title?: string;
}

interface JobListItem {
  id?: string;
  title?: string;
  status?: string;
}

interface JobsListResult {
  jobs?: JobListItem[];
  count?: number;
}

interface ErrorLogEntry {
  time?: string;
  message?: string;
  data?: {
    error_type?: string;
    technical_message?: string;
  };
}

interface JobStatusResult {
  id?: string;
  status?: string;
  progress?: number;
  error_message?: string;
  errorMessage?: string;
  error_logs?: ErrorLogEntry[];
  errorLogs?: ErrorLogEntry[];
  created?: string;
}

interface ResultsResult {
  error?: string;
  format?: string;
  size_bytes?: number;
  statistics?: {
    min?: number;
    max?: number;
    mean?: number;
  };
}

interface QualityResult {
  cloud_coverage_percent?: number;
  cloudCoverage?: number;
  cloud_coverage?: { estimated_cloud_cover_pct?: number };
  temporal_coverage_percent?: number;
  temporalCoverage?: number;
  temporal_coverage?: { coverage_percentage?: number };
  valid_pixels_percent?: number;
  validPixelPercentage?: number;
  spatial_completeness_pct?: number;
  recommendations?: string[];
}

interface SavedJobItem {
  save_id?: string;
  title?: string;
  size_bytes?: number;
}

interface SavedJobsResult {
  jobs?: SavedJobItem[];
  count?: number;
}

interface ValidationResult {
  valid?: boolean;
  errors?: string[];
  warnings?: string[];
  resource_estimate?: {
    estimated_size_mb?: number;
    estimated_pixels?: number;
  };
}

interface VizAckResult {
  type?: string;
}

// --- Shared card shell ---

function CardShell({
  icon: Icon,
  iconColor,
  title,
  children,
}: {
  icon: React.ElementType;
  iconColor: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-card/50 overflow-hidden text-xs">
      <div className="flex items-center gap-2 border-b border-border/40 bg-muted/30 px-3 py-1.5">
        <Icon className={cn("h-3.5 w-3.5 shrink-0", iconColor)} />
        <span className="font-medium text-foreground/80 truncate">{title}</span>
      </div>
      <div className="px-3 py-2">{children}</div>
    </div>
  );
}

// --- Status badge helper ---

function StatusBadge({ status }: { status: string }) {
  const s = status?.toLowerCase() ?? "";
  const variant =
    s === "finished"
      ? "success"
      : s === "error"
        ? "destructive"
        : s === "running"
          ? "default"
          : "secondary";
  return (
    <Badge variant={variant} className="text-[10px] px-1.5 py-0">
      {status}
    </Badge>
  );
}

// --- Per-tool renderers ---

function CollectionsCard({ result }: { result: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const items: CollectionItem[] = Array.isArray(result) ? result : [];
  const visible = expanded ? items : items.slice(0, 4);

  return (
    <CardShell
      icon={Database}
      iconColor="text-blue-500"
      title={`Collections (${items.length})`}
    >
      <div className="flex flex-col gap-1">
        {visible.map((c, i) => (
          <div key={c.id ?? i} className="flex items-baseline gap-2 min-w-0">
            <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] font-mono text-primary">
              {c.id}
            </code>
            <span className="truncate text-muted-foreground">
              {c.title || c.description || ""}
            </span>
          </div>
        ))}
      </div>
      {items.length > 4 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1.5 text-[11px] text-primary hover:underline"
        >
          {expanded ? "Show less" : `+${items.length - 4} more`}
        </button>
      )}
    </CardShell>
  );
}

function CollectionInfoCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return (
      <GenericCard toolName="openeo_get_collection_info" result={result} />
    );

  const r = result as CollectionInfoResult;
  const bands = r.bands ? Object.keys(r.bands) : [];
  const bandNames =
    bands.slice(0, 6).join(", ") +
    (bands.length > 6 ? `, +${bands.length - 6}` : "");
  const temporal = r.extent?.temporal?.interval?.[0];
  const tempStr = temporal
    ? `${temporal[0] || "?"} — ${temporal[1] || "present"}`
    : null;

  return (
    <CardShell
      icon={Layers}
      iconColor="text-violet-500"
      title={r.id || "Collection"}
    >
      <div className="flex flex-col gap-1 text-muted-foreground">
        {r.title && (
          <div className="font-medium text-foreground/80">{r.title}</div>
        )}
        {bands.length > 0 && (
          <div>
            {bands.length} bands: {bandNames}
          </div>
        )}
        {tempStr && <div>Temporal: {tempStr}</div>}
      </div>
    </CardShell>
  );
}

function LocationCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_resolve_location" result={result} />;

  const r = result as LocationResult;
  // bbox may be nested under r.bbox or r.spatial_extent, or flat at top level
  const bbox = r.bbox ?? r.spatial_extent ?? r;
  const w = bbox.west ?? bbox.W;
  const s = bbox.south ?? bbox.S;
  const e = bbox.east ?? bbox.E;
  const n = bbox.north ?? bbox.N;
  const label = r.location ?? r.name ?? "";

  return (
    <CardShell
      icon={MapPin}
      iconColor="text-emerald-500"
      title={label ? `Location: ${label}` : "Location Resolved"}
    >
      <div className="grid grid-cols-4 gap-x-3 gap-y-0.5 text-muted-foreground font-mono text-[11px]">
        <span>W: {Number(w)?.toFixed(2)}</span>
        <span>S: {Number(s)?.toFixed(2)}</span>
        <span>E: {Number(e)?.toFixed(2)}</span>
        <span>N: {Number(n)?.toFixed(2)}</span>
      </div>
    </CardShell>
  );
}

function ProcessGraphCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_generate_graph" result={result} />;

  const nodes = Object.entries(result).map(([id, node]: [string, unknown]) => ({
    id,
    process: (node as ProcessGraphNode)?.process_id || id,
  }));

  return (
    <CardShell
      icon={GitBranch}
      iconColor="text-orange-500"
      title={`Process Graph (${nodes.length} nodes)`}
    >
      <div className="flex flex-wrap items-center gap-1">
        {nodes.map((n, i) => (
          <span key={n.id} className="flex items-center gap-1">
            <code className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-mono text-foreground/80">
              {n.process}
            </code>
            {i < nodes.length - 1 && (
              <span className="text-muted-foreground/50">&rarr;</span>
            )}
          </span>
        ))}
      </div>
    </CardShell>
  );
}

function JobCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_create_job" result={result} />;

  const r = result as JobResult;
  return (
    <CardShell icon={Play} iconColor="text-primary" title="Job Created">
      <div className="flex flex-col gap-1 text-muted-foreground">
        <div className="flex items-center gap-2">
          <code className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-mono">
            {r.id || "\u2014"}
          </code>
          <StatusBadge status={r.status || "created"} />
        </div>
        {r.title && <div>{r.title}</div>}
      </div>
    </CardShell>
  );
}

function JobsListCard({ result }: { result: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const r = isRecord(result) ? (result as JobsListResult) : null;
  const jobs: JobListItem[] = r?.jobs || (Array.isArray(result) ? result : []);
  const count = r?.count ?? jobs.length;
  const visible = expanded ? jobs : jobs.slice(0, 5);

  return (
    <CardShell
      icon={ListChecks}
      iconColor="text-foreground/70"
      title={`Jobs (${count})`}
    >
      <div className="flex flex-col gap-1">
        {visible.map((j, i) => (
          <div key={j.id ?? i} className="flex items-center gap-2 min-w-0">
            <code className="shrink-0 rounded bg-muted px-1 py-0.5 text-[10px] font-mono text-muted-foreground">
              {(j.id || "").slice(0, 8)}
            </code>
            <span className="truncate text-muted-foreground flex-1">
              {j.title || "\u2014"}
            </span>
            <StatusBadge status={j.status || "\u2014"} />
          </div>
        ))}
      </div>
      {jobs.length > 5 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1.5 text-[11px] text-primary hover:underline"
        >
          {expanded ? "Show less" : `+${jobs.length - 5} more`}
        </button>
      )}
    </CardShell>
  );
}

function JobStatusCard({ result }: { result: unknown }) {
  const [logsExpanded, setLogsExpanded] = useState(false);
  if (!isRecord(result))
    return <GenericCard toolName="openeo_get_job_status" result={result} />;

  const r = result as JobStatusResult;
  const progress = r.progress != null ? Number(r.progress) : null;
  const isError = r.status?.toLowerCase() === "error";
  const errorMessage = r.error_message || r.errorMessage;
  const errorLogs: ErrorLogEntry[] = r.error_logs || r.errorLogs || [];

  return (
    <CardShell
      icon={isError ? XCircle : Clock}
      iconColor={isError ? "text-destructive" : "text-foreground/70"}
      title={isError ? "Job Failed" : "Job Status"}
    >
      <div className="flex flex-col gap-1.5 text-muted-foreground">
        <div className="flex items-center gap-2">
          <code className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-mono">
            {r.id || "\u2014"}
          </code>
          <StatusBadge status={r.status || "\u2014"} />
        </div>
        {progress != null && (
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.min(progress, 100)}%` }}
              />
            </div>
            <span className="text-[10px] tabular-nums">{progress}%</span>
          </div>
        )}
        {isError && errorMessage && (
          <div className="flex items-start gap-1.5 mt-1 rounded bg-destructive/10 border border-destructive/20 px-2 py-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-destructive mt-0.5" />
            <span className="text-[11px] text-destructive break-words">
              {errorMessage}
            </span>
          </div>
        )}
        {isError && errorLogs.length > 0 && (
          <div className="flex flex-col gap-1">
            <button
              onClick={() => setLogsExpanded(!logsExpanded)}
              className="text-[10px] text-primary hover:underline text-left"
            >
              {logsExpanded
                ? "Hide logs"
                : `Show error logs (${errorLogs.length})`}
            </button>
            {logsExpanded && (
              <div className="flex flex-col gap-1 max-h-40 overflow-auto rounded bg-muted/50 border border-border/40 px-2 py-1.5">
                {errorLogs.map((log, i) => (
                  <div key={i} className="flex flex-col gap-0.5">
                    {log.time && (
                      <span className="text-[9px] text-muted-foreground/60 font-mono">
                        {log.time}
                      </span>
                    )}
                    <span className="text-[10px] text-destructive/90 font-mono break-words">
                      {log.message}
                    </span>
                    {log.data?.error_type && (
                      <span className="text-[9px] text-muted-foreground/60">
                        Type: {log.data.error_type}
                      </span>
                    )}
                    {log.data?.technical_message &&
                      log.data.technical_message !== log.message && (
                        <span className="text-[9px] text-muted-foreground/80 font-mono break-words">
                          {log.data.technical_message}
                        </span>
                      )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {r.created && (
          <div className="text-[10px]">Created: {formatDate(r.created)}</div>
        )}
      </div>
    </CardShell>
  );
}

function ResultsCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_get_results" result={result} />;

  const r = result as ResultsResult;
  if (r.error) {
    return (
      <CardShell icon={XCircle} iconColor="text-destructive" title="Results">
        <div className="text-destructive">{r.error}</div>
      </CardShell>
    );
  }

  const stats = r.statistics;
  return (
    <CardShell
      icon={Download}
      iconColor="text-emerald-500"
      title="Results Ready"
    >
      <div className="flex flex-col gap-1 text-muted-foreground">
        <div className="flex items-center gap-3">
          {r.format && (
            <span>
              Format: <strong className="text-foreground/80">{r.format}</strong>
            </span>
          )}
          {r.size_bytes != null && (
            <span>
              Size:{" "}
              <strong className="text-foreground/80">
                {formatBytes(r.size_bytes)}
              </strong>
            </span>
          )}
        </div>
        {stats && (
          <div className="flex items-center gap-3 font-mono text-[11px]">
            {stats.min != null && (
              <span>min: {Number(stats.min).toFixed(3)}</span>
            )}
            {stats.max != null && (
              <span>max: {Number(stats.max).toFixed(3)}</span>
            )}
            {stats.mean != null && (
              <span>mean: {Number(stats.mean).toFixed(3)}</span>
            )}
          </div>
        )}
      </div>
    </CardShell>
  );
}

function QualityCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_quality_metrics" result={result} />;

  const r = result as QualityResult;
  const cloud = r?.cloud_coverage?.estimated_cloud_cover_pct ?? r?.cloud_coverage_percent ?? r?.cloudCoverage ?? null;
  const temporal = r?.temporal_coverage?.coverage_percentage ?? r?.temporal_coverage_percent ?? r?.temporalCoverage ?? null;
  const spatial = r?.spatial_completeness_pct ?? r?.valid_pixels_percent ?? r?.validPixelPercentage ?? null;
  const metrics = [
    {
      label: "Cloud",
      value: cloud,
      color: "text-blue-400",
    },
    {
      label: "Temporal",
      value: temporal,
      color: "text-emerald-400",
    },
    {
      label: "Valid Px",
      value: spatial,
      color: "text-amber-400",
    },
  ].filter((m) => m.value != null);

  const recs: string[] = r.recommendations || [];

  return (
    <CardShell
      icon={BarChart3}
      iconColor="text-foreground/70"
      title="Quality Metrics"
    >
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-3">
          {metrics.map((m) => (
            <div key={m.label} className="flex items-center gap-1.5">
              <span className="text-muted-foreground">{m.label}:</span>
              <span className={cn("font-semibold tabular-nums", m.color)}>
                {Number(m.value).toFixed(0)}%
              </span>
            </div>
          ))}
        </div>
        {recs.length > 0 && (
          <div className="flex flex-col gap-0.5">
            {recs.slice(0, 2).map((rec, i) => (
              <div
                key={i}
                className="flex items-start gap-1 text-muted-foreground"
              >
                <AlertTriangle className="h-3 w-3 shrink-0 text-yellow-500 mt-0.5" />
                <span className="line-clamp-1">{rec}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </CardShell>
  );
}

function SavedJobsCard({ result }: { result: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const r = isRecord(result) ? (result as SavedJobsResult) : null;
  const jobs: SavedJobItem[] = r?.jobs || [];
  const count = r?.count ?? jobs.length;
  const visible = expanded ? jobs : jobs.slice(0, 4);

  return (
    <CardShell
      icon={Archive}
      iconColor="text-foreground/70"
      title={`Saved Results (${count})`}
    >
      <div className="flex flex-col gap-1">
        {visible.map((j, i) => (
          <div key={j.save_id ?? i} className="flex items-center gap-2 min-w-0">
            <span className="truncate flex-1 text-muted-foreground">
              {j.title || j.save_id}
            </span>
            {j.size_bytes != null && (
              <span className="shrink-0 text-[10px] text-muted-foreground/60">
                {formatBytes(j.size_bytes)}
              </span>
            )}
          </div>
        ))}
      </div>
      {jobs.length > 4 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1.5 text-[11px] text-primary hover:underline"
        >
          {expanded ? "Show less" : `+${jobs.length - 4} more`}
        </button>
      )}
    </CardShell>
  );
}

function ValidationCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_validate_graph" result={result} />;

  const r = result as ValidationResult;
  const valid = r.valid !== false;
  const errors: string[] = r.errors || [];
  const warnings: string[] = r.warnings || [];
  const est = r.resource_estimate;

  return (
    <CardShell
      icon={valid ? CheckCircle : XCircle}
      iconColor={valid ? "text-emerald-500" : "text-destructive"}
      title={valid ? "Validation Passed" : "Validation Failed"}
    >
      <div className="flex flex-col gap-1 text-muted-foreground">
        {errors.length > 0 &&
          errors.slice(0, 3).map((e, i) => (
            <div key={i} className="flex items-start gap-1">
              <XCircle className="h-3 w-3 shrink-0 text-destructive mt-0.5" />
              <span className="line-clamp-2">{e}</span>
            </div>
          ))}
        {warnings.length > 0 &&
          warnings.slice(0, 2).map((w, i) => (
            <div key={i} className="flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 shrink-0 text-yellow-500 mt-0.5" />
              <span className="line-clamp-1">{w}</span>
            </div>
          ))}
        {est && (
          <div className="text-[10px] mt-0.5">
            {est.estimated_size_mb != null && (
              <span>
                Est. size: {Number(est.estimated_size_mb).toFixed(1)} MB
              </span>
            )}
            {est.estimated_pixels != null && (
              <span className="ml-2">
                Pixels: {Number(est.estimated_pixels).toLocaleString()}
              </span>
            )}
          </div>
        )}
      </div>
    </CardShell>
  );
}

function EstimateCard({ result }: { result: unknown }) {
  if (!isRecord(result))
    return <GenericCard toolName="openeo_estimate_extent" result={result} />;

  const entries = Object.entries(result)
    .filter(([k]) => !k.startsWith("_"))
    .slice(0, 6);

  return (
    <CardShell
      icon={FileText}
      iconColor="text-foreground/70"
      title="Extent Estimate"
    >
      <div className="flex flex-col gap-0.5 text-muted-foreground font-mono text-[11px]">
        {entries.map(([k, v]) => (
          <div key={k} className="truncate">
            <span className="text-primary/70">{k}</span>:{" "}
            {typeof v === "number" ? v.toLocaleString() : String(v)}
          </div>
        ))}
      </div>
    </CardShell>
  );
}

function VizAckCard({ result }: { result: unknown }) {
  const r = isRecord(result) ? (result as VizAckResult) : null;
  const isChart = r?.type === "chart";
  return (
    <CardShell
      icon={isChart ? BarChart3 : Globe}
      iconColor={isChart ? "text-violet-500" : "text-emerald-500"}
      title={isChart ? "Time Series" : "Map Visualization"}
    >
      <div className="text-muted-foreground italic">
        Rendered in visualization panel &rarr;
      </div>
    </CardShell>
  );
}

function GenericCard({
  toolName,
  result,
}: {
  toolName: string;
  result: unknown;
}) {
  const [expanded, setExpanded] = useState(false);
  const friendlyName = toolName
    .replace(/^openeo_/, "")
    .replace(/^viz_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const json =
    typeof result === "string"
      ? result
      : JSON.stringify(result, null, expanded ? 2 : 0);
  const preview = (json || "").slice(0, 120);

  return (
    <CardShell
      icon={Wrench}
      iconColor="text-muted-foreground"
      title={friendlyName}
    >
      <div className="text-muted-foreground">
        {expanded ? (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px]">
            {json}
          </pre>
        ) : (
          <span className="font-mono text-[11px]">
            {preview}
            {json.length > 120 ? "..." : ""}
          </span>
        )}
      </div>
      {json.length > 120 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1 text-[11px] text-primary hover:underline"
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      )}
    </CardShell>
  );
}

// --- Utility: parse JSON strings into objects ---

function tryParseJson(value: unknown): unknown {
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

// --- Router ---

const RENDERERS: Record<string, React.FC<{ result: unknown }>> = {
  openeo_list_collections: CollectionsCard,
  openeo_get_collection_info: CollectionInfoCard,
  openeo_resolve_location: LocationCard,
  openeo_generate_graph: ProcessGraphCard,
  openeo_create_job: JobCard,
  openeo_list_jobs: JobsListCard,
  openeo_get_job_status: JobStatusCard,
  openeo_get_results: ResultsCard,
  openeo_quality_metrics: QualityCard,
  saved_jobs_list: SavedJobsCard,
  openeo_validate_graph: ValidationCard,
  openeo_estimate_extent: EstimateCard,
  viz_show_map: VizAckCard,
  viz_show_time_series: VizAckCard,
};

interface ToolResultCardProps {
  toolName: string;
  result: unknown;
}

export const ToolResultCard = memo(function ToolResultCard({
  toolName,
  result: rawResult,
}: ToolResultCardProps) {
  const result = tryParseJson(rawResult);
  const Renderer = RENDERERS[toolName];
  if (Renderer) return <Renderer result={result} />;
  return <GenericCard toolName={toolName} result={result} />;
});
