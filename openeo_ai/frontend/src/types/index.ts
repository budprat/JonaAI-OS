// Thinking step for displaying AI processing
export interface ThinkingStep {
  id: string;
  type: "analyzing" | "planning" | "executing" | "processing" | "fetching" | "validating";
  message: string;
  timestamp: Date;
  completed?: boolean;
}

// Message types
export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: Date;
  toolCalls?: ToolCall[];
  visualizations?: Visualization[];
  thinkingSteps?: ThinkingStep[];
  isStreaming?: boolean;
  toolName?: string;
  toolResult?: unknown;
  /** Whether this error message can be retried by the user. */
  isRetryable?: boolean;
  /**
   * Langfuse trace ID for this assistant response (Phase D observability).
   * Set by useWebSocket from the `trace` WS event so ChatMessage can post
   * user feedback scores to the correct trace.
   */
  traceId?: string;
  /** Numbers in this answer that no tool result this turn supports (flagged, never blocked). */
  groundingWarnings?: GroundingViolation[];
  /** Source URLs the turn's tools reported. */
  sources?: string[];
}

/** One number the grounding check couldn't trace to a tool result. */
export interface GroundingViolation {
  value: string | number;
  token: string;
  context: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  status: "pending" | "running" | "completed" | "error";
}

// Visualization types
export type VisualizationType =
  | "map"
  | "chart"
  | "comparison"
  | "table"
  | "process_graph"
  | "multi_panel"
  | "dashboard"
  | "timelapse"
  | "classification";

export interface MultiPanelVisualization {
  panels: {
    label: string;
    visualization: MapVisualization;
  }[];
}

export interface DashboardVisualization {
  panels: {
    type: "map" | "chart" | "table" | "stat";
    data: MapVisualization | ChartVisualization | TableVisualization | StatCardData;
    title?: string;
    gridArea?: string;
  }[];
}

export interface StatCardData {
  metrics: { label: string; value: string | number; unit?: string }[];
}

export interface TimelapseVisualization {
  layers: {
    timestamp: string;
    url: string;
    bounds?: [[number, number], [number, number]];
    colormap?: string;
    bandIndex?: number;
    stats?: { min: number; max: number; mean: number };
  }[];
  colormap?: string;
}

export interface Visualization {
  id: string;
  type: VisualizationType;
  title?: string;
  saveId?: string;
  data:
    | MapVisualization
    | ChartVisualization
    | ComparisonVisualization
    | TableVisualization
    | ProcessGraphVisualization
    | MultiPanelVisualization
    | DashboardVisualization
    | TimelapseVisualization
    | ClassificationVisualization;
}

/** A section captured from analysis tools (report, interpretation, Earth-Agent). */
export interface ReportSection {
  id: string;
  type: "report" | "interpretation" | "earth_agent" | "statistics" | "quality" | "method_card";
  title: string;
  content: string;
  timestamp: Date;
  toolName: string;
}

export interface RasterStats {
  min: number;
  max: number;
  mean: number;
  std: number;
  median?: number;
  nodata_percent: number;
  valid_pixels?: number;
  total_pixels?: number;
  crs?: string;
  resolution?: [number, number];
  width?: number;
  height?: number;
  bands?: number;
  dtype?: string;
  file_size_bytes?: number;
}

export interface MapVisualization {
  type: "raster" | "vector";
  url?: string;
  bounds?: [[number, number], [number, number]];
  colormap?: string;
  opacity?: number;
  geojson?: GeoJSON.FeatureCollection;
  vmin?: number;
  vmax?: number;
  source?: string;
  colorbar?: { min: number; max: number; colormap: string };
  stats?: RasterStats;
}

export interface ChartVisualization {
  chartType: "line" | "bar" | "area" | "scatter";
  title: string;
  xAxis: {
    label: string;
    values: (string | number)[];
  };
  yAxis: {
    label: string;
    range?: [number, number];
  };
  series: {
    name: string;
    values: number[];
    color?: string;
  }[];
}

export interface ComparisonVisualization {
  before: MapVisualization;
  after: MapVisualization;
  labels?: {
    before: string;
    after: string;
  };
}

export interface TableVisualization {
  headers: string[];
  rows: (string | number)[][];
}

export interface ClassificationVisualization {
  label: string;
  confidence: number;
  categories?: { name: string; value: number; color?: string }[];
}

export interface ProcessGraphVisualization {
  nodes: ProcessNode[];
  edges: ProcessEdge[];
}

export interface ProcessNode {
  id: string;
  process_id: string;
  arguments: Record<string, unknown>;
  result?: boolean;
  position?: { x: number; y: number };
}

export interface ProcessEdge {
  source: string;
  target: string;
  argument: string;
}

// Quality metrics
export interface QualityMetrics {
  overallScore: number;
  grade: string;
  cloudCoverage: number;
  temporalCoverage: number;
  spatialCoverage: number;
  validPixelPercentage: number;
  recommendations: string[];
}

// Sustainability metrics
export interface SustainabilityMetrics {
  carbonFootprint: number; // kg CO2
  dataTransferred: number; // bytes
  computeTime: number; // seconds
  energyUsed: number; // kWh
}

// Workflow
export interface Workflow {
  id: string;
  name: string;
  description?: string;
  status: "idle" | "running" | "completed" | "error" | "stopped";
  steps: WorkflowStep[];
  processGraph?: Record<string, unknown>;
  createdAt: Date;
  completedAt?: Date;
}

export interface WorkflowStep {
  id: string;
  name: string;
  status: "pending" | "running" | "completed" | "error";
  tool?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
  startedAt?: Date;
  completedAt?: Date;
}

// WebSocket message types
export interface WSMessage {
  type:
    | "message"
    | "tool_start"
    | "tool_result"
    | "visualization"
    | "quality_metrics"
    | "error"
    | "status";
  data: unknown;
}

// Collection types
export interface Collection {
  id: string;
  title: string;
  description?: string;
  extent: {
    spatial: { bbox: number[][] };
    temporal: { interval: string[][] };
  };
  keywords?: string[];
}

// Process types
export interface Process {
  id: string;
  summary: string;
  description?: string;
  parameters: ProcessParameter[];
  returns: ProcessReturn;
}

export interface ProcessParameter {
  name: string;
  description: string;
  schema: Record<string, unknown>;
  required?: boolean;
  default?: unknown;
}

export interface ProcessReturn {
  description: string;
  schema: Record<string, unknown>;
}

// Bounding box
export interface BBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

// Saved job from persistent archive
export interface SavedJob {
  save_id: string;
  title: string;
  result_path: string;
  original_path?: string;
  bounds?: number[][];
  colormap: string;
  vmin?: number;
  vmax?: number;
  size_bytes: number;
  source_query?: string;
  job_id?: string;
  project_id?: string;
  created_at: string;
  /** False when the files are missing on this server (the saved-results list only). */
  available?: boolean;
  /** True when an offsite copy exists; opening the result restores it from there. */
  restorable?: boolean;
}

// Project
export interface JobFile {
  name: string;
  path: string;
  ext: string;
  size_bytes: number;
  modified: string;
  download_url?: string;
}

export interface ProjectFile {
  save_id: string;
  title: string;
  result_path: string;
  bounds?: number[][];
  colormap?: string;
  size_bytes: number;
  created_at: string;
  project_id?: string;
  job_files?: JobFile[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  user_id?: string;
  analysis_count: number;
  files?: ProjectFile[];
  created_at: string;
  updated_at: string;
}

export interface EarthAsset {
  id: string;
  user_id?: string;
  project_id?: string | null;
  name: string;
  asset_type: string;
  geometry?: Record<string, unknown> | null;
  bbox?: number[] | null;
  properties: Record<string, unknown>;
  external_refs: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface EarthWatch {
  id: string;
  user_id?: string;
  project_id?: string | null;
  asset_id: string;
  name: string;
  description?: string | null;
  status: string;
  template_id?: string | null;
  process_graph: Record<string, unknown>;
  scf?: Record<string, unknown> | null;
  collections: string[];
  trigger_type: string;
  cadence?: string | null;
  temporal_policy: Record<string, unknown>;
  quality_policy: Record<string, unknown>;
  budget_policy: Record<string, unknown>;
  next_run_at?: string | null;
  last_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EarthObservation {
  id: string;
  user_id?: string;
  watch_id?: string | null;
  asset_id?: string | null;
  status: string;
  window_start?: string | null;
  window_end?: string | null;
  source_catalog_item_ids: string[];
  openeo_job_id?: string | null;
  api_job_id?: string | null;
  process_graph_hash?: string | null;
  scf_hash?: string | null;
  quality_metrics: Record<string, unknown>;
  cost_estimate: Record<string, unknown>;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

export interface ObservationEvent {
  id: string;
  user_id?: string;
  observation_id: string;
  event_type: string;
  message?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CatalogItem {
  id: string;
  provider: string;
  collection_id: string;
  stac_id: string;
  datetime?: string | null;
  geometry?: Record<string, unknown> | null;
  bbox?: number[] | null;
  cloud_cover?: number | null;
  assets: Record<string, unknown>;
  properties: Record<string, unknown>;
  ingested_at: string;
}

export interface EvidencePackage {
  id: string;
  user_id?: string;
  observation_id?: string | null;
  saved_result_id?: string | null;
  saved_result_save_id?: string | null;
  stac_item: Record<string, unknown>;
  source_items: Record<string, unknown>[];
  artifacts: Record<string, unknown>;
  statistics: Record<string, unknown>;
  provenance: Record<string, unknown>;
  process_graph: Record<string, unknown>;
  scf?: Record<string, unknown> | null;
  trace_id?: string | null;
  created_at: string;
}

export interface EarthBaseline {
  id: string;
  user_id?: string;
  asset_id: string;
  watch_id?: string | null;
  metric: string;
  season_key?: string | null;
  window_policy: Record<string, unknown>;
  stats: Record<string, unknown>;
  sample_count: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface EarthSignal {
  id: string;
  user_id?: string;
  asset_id?: string | null;
  watch_id?: string | null;
  observation_id?: string | null;
  signal_type: string;
  metric?: string | null;
  value?: number | null;
  baseline_value?: number | null;
  delta?: number | null;
  severity: string;
  coverage?: number | null; // Renamed from confidence (data coverage, not calibrated confidence)
  geometry?: Record<string, unknown> | null;
  status: string;
  explanation?: string | null;
  created_at: string;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
}

export interface Delivery {
  id: string;
  user_id?: string;
  signal_id: string;
  channel: string;
  target: string;
  status: string;
  attempt_count: number;
  last_error?: string | null;
  sent_at?: string | null;
  created_at: string;
}

export interface SavedJobValidation {
  is_valid: boolean;
  warnings: string[];
}

export interface SavedJobProjectSummary {
  id: string;
  name: string;
  description: string;
  analysis_count: number;
  created_at: string;
  updated_at: string;
}

export interface SavedJobInterpretation {
  grade?: string;
  grade_score?: number;
  mean_category?: string;
  summary?: string;
  category_breakdown?: Array<{
    category: string;
    range: string;
    est_area_pct: number;
    assessment: string;
  }>;
  tips?: string[];
  warnings?: string[];
  domain?: string;
  index?: string;
}

export interface SavedJobQualityAssessment {
  grade?: string;
  score?: number;
  flags?: string[];
  summary?: string;
}

export interface SavedJobEarthIntelligenceSummary {
  evidence_count: number;
  evidence_package_ids: string[];
  observation?: EarthObservation | null;
  watch?: EarthWatch | null;
  asset?: EarthAsset | null;
}

export interface SavedJobDetail extends SavedJob {
  metadata?: Record<string, unknown> | null;
  statistics?: Record<string, unknown> | null;
  validation?: SavedJobValidation | null;
  interpretation?: SavedJobInterpretation | null;
  quality_assessment?: SavedJobQualityAssessment | null;
  interpreted_index?: string | null;
  project?: SavedJobProjectSummary | null;
  result_filename?: string;
  result_ext?: string;
  download_url?: string;
  job_files?: JobFile[];
  evidence_packages?: EvidencePackage[];
  earth_intelligence?: SavedJobEarthIntelligenceSummary;
}

// Clarification (AskUserQuestion)
export interface ClarificationOption {
  label: string;
  description: string;
}

export interface ClarificationQuestion {
  question: string;
  header: string;
  options: ClarificationOption[];
  multiSelect?: boolean;
}

// Export types
export type ExportFormat = "notebook" | "json" | "markdown" | "bibtex";

export interface ExportOptions {
  format: ExportFormat;
  includeVisualizations: boolean;
  includeQualityMetrics: boolean;
  includeProcessGraph: boolean;
}

// AI SDK v5-inspired types (Phase 1: type-only, no runtime changes)
export type {
  MessageRole,
  ToolCallState,
  TypedToolCall,
  MessagePart,
  TextPart,
  ToolCallPart,
  ToolResultPart,
  VisualizationPart,
  ThinkingPart,
  QualityMetricsPart,
  SustainabilityPart,
  SuggestionsPart,
  ClarificationPart,
  ProcessGraphPart,
  UIMessage,
  MessageMetadata,
  BackendWsMessage,
} from "./ai-sdk";

export {
  isTextPart,
  isToolCallPart,
  isToolResultPart,
  isVisualizationPart,
  isThinkingPart,
  isSuggestionsPart,
  isClarificationPart,
  isProcessGraphPart,
  isDataPart,
  extractParts,
  extractAllParts,
  getTextContent,
  hasActiveThinking,
} from "./ai-sdk";
