import React, { useCallback, useEffect, useState } from "react";
import {
  Search,
  GitBranch,
  Play,
  Clock,
  RefreshCw,
  Calendar,
  Layers,
  Tag,
  Pause,
  AlertCircle,
  CheckCircle2,
  Loader2,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/auth/useAuth";
import { useTemplates } from "@/hooks/usePlatformData";

/* ── API Types (mirrors batch/models.py) ── */

interface TemplateParameter {
  name: string;
  type: string;
  description: string;
  default_value: unknown;
  required: boolean;
}

interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  process_graph: Record<string, unknown>;
  parameters: TemplateParameter[];
  tags: string[];
  source_query: string;
  collection_id: string;
  index_type: string;
  version: number;
  user_id: string;
  use_count: number;
  created_at: string;
  updated_at: string;
}

interface BatchJob {
  id: string;
  template_id: string;
  name: string;
  status: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  trigger_type: string;
  project_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  estimated_duration_seconds: number | null;
}

interface Schedule {
  id: string;
  template_id: string;
  name: string;
  frequency: string;
  status: string;
  locations_count: number;
  runs_completed: number;
  max_runs: number | null;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
}

type ActiveTab = "templates" | "jobs" | "schedules";

/* ── Status badge helper ── */

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { icon: React.ElementType; color: string }> = {
    completed: { icon: CheckCircle2, color: "text-status-good" },
    running: { icon: Loader2, color: "text-blue-500" },
    pending: { icon: Clock, color: "text-muted-foreground" },
    queued: { icon: Clock, color: "text-muted-foreground" },
    failed: { icon: XCircle, color: "text-status-critical" },
    cancelled: { icon: XCircle, color: "text-muted-foreground" },
    partially_completed: { icon: AlertCircle, color: "text-status-warn" },
    active: { icon: CheckCircle2, color: "text-status-good" },
    paused: { icon: Pause, color: "text-status-warn" },
  };
  const c = config[status] || { icon: Clock, color: "text-muted-foreground" };
  const Icon = c.icon;
  return (
    <span className={`inline-flex items-center gap-1 font-mono text-[10px] uppercase ${c.color}`}>
      <Icon className={`h-3 w-3 ${status === "running" ? "animate-spin" : ""}`} />
      {status}
    </span>
  );
}

/* ── Main Component ── */

const WorkflowBuilder: React.FC = () => {
  const { accessToken: authToken } = useAuth();
  const [activeTab, setActiveTab] = useState<ActiveTab>("templates");
  const [searchQuery, setSearchQuery] = useState("");

  const templatesHook = useTemplates(100);

  // Batch jobs and schedules stay direct REST calls.
  const [jobs, setJobs] = useState<BatchJob[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const templates = templatesHook.templates as unknown as WorkflowTemplate[];

  // Selection
  const [selectedTemplate, setSelectedTemplate] = useState<WorkflowTemplate | null>(null);
  const [selectedJob, setSelectedJob] = useState<BatchJob | null>(null);

  const headers: HeadersInit = authToken ? { Authorization: `Bearer ${authToken}` } : {};

  // Fetch REST data for jobs and schedules; templates come from useTemplates.
  const fetchAll = useCallback(async () => {
    if (!authToken) return;
    setLoading(true);
    setError(null);
    try {
      // Always fetch jobs and schedules via REST
      const [jobsRes, schedRes] = await Promise.all([
        fetch("/api/batch/jobs?limit=50", { headers }),
        fetch("/api/batch/schedules?limit=50", { headers }),
      ]);

      if (jobsRes.ok) setJobs(await jobsRes.json());
      if (schedRes.ok) setSchedules(await schedRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load workflow data");
    } finally {
      setLoading(false);
    }
  }, [authToken]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  const isLoadingTemplates = templatesHook.isLoading;

  // Filtered templates by search
  const filteredTemplates = searchQuery
    ? templates.filter(
        (t) =>
          t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          t.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
          t.tags.some((tag) => tag.toLowerCase().includes(searchQuery.toLowerCase())) ||
          t.index_type.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : templates;

  // Format date
  const fmtDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-border px-6 py-4">
        <div className="flex items-center gap-3">
          <Search className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search templates by name, tag, or index type..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <Button variant="ghost" size="sm" onClick={fetchAll} className="flex-shrink-0">
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex-shrink-0 border-b border-border px-6">
        <div className="flex gap-1">
          {(
            [
              {
                id: "templates" as const,
                label: "Templates",
                icon: Layers,
                count: templates.length,
              },
              { id: "jobs" as const, label: "Batch Jobs", icon: Play, count: jobs.length },
              {
                id: "schedules" as const,
                label: "Schedules",
                icon: Calendar,
                count: schedules.length,
              },
            ] as const
          ).map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-xs font-medium transition-colors ${
                  activeTab === tab.id
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {tab.label}
                {tab.count > 0 && (
                  <span className="rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                    {tab.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: List */}
        <div className="w-80 flex-shrink-0 overflow-auto border-r border-border bg-card p-4">
          {(activeTab === "templates" ? isLoadingTemplates : loading) ? (
            <div className="flex flex-col items-center gap-2 py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              <span className="text-xs text-muted-foreground">Loading...</span>
            </div>
          ) : (activeTab === "templates" ? templatesHook.error : error) ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-center">
              <p className="text-xs text-destructive">
                {activeTab === "templates" ? templatesHook.error : error}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={activeTab === "templates" ? templatesHook.refetch : fetchAll}
              >
                Retry
              </Button>
            </div>
          ) : activeTab === "templates" ? (
            filteredTemplates.length === 0 ? (
              <EmptyState
                icon={Layers}
                title="No Templates Yet"
                description="Run an analysis in the AI Chat workspace, then save the process graph as a reusable template."
              />
            ) : (
              <div className="space-y-2">
                {filteredTemplates.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => {
                      setSelectedTemplate(t);
                      setSelectedJob(null);
                    }}
                    className={`block w-full rounded-md border p-3 text-left transition-colors ${
                      selectedTemplate?.id === t.id
                        ? "border-primary bg-accent"
                        : "border-border hover:bg-accent/50"
                    }`}
                  >
                    <div className="mb-1 text-sm font-medium leading-tight">{t.name}</div>
                    <p className="line-clamp-2 text-xs text-muted-foreground">{t.description}</p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {t.tags.slice(0, 4).map((tag) => (
                        <span
                          key={tag}
                          className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                      {t.index_type && (
                        <span className="rounded-md bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] uppercase text-primary">
                          {t.index_type}
                        </span>
                      )}
                    </div>
                    <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-muted-foreground">
                      <span>v{t.version}</span>
                      <span>{t.use_count} runs</span>
                    </div>
                  </button>
                ))}
              </div>
            )
          ) : activeTab === "jobs" ? (
            jobs.length === 0 ? (
              <EmptyState
                icon={Play}
                title="No Batch Jobs"
                description="Submit a batch job from a template to run analysis across multiple locations."
              />
            ) : (
              <div className="space-y-2">
                {jobs.map((j) => (
                  <button
                    key={j.id}
                    onClick={() => {
                      setSelectedJob(j);
                      setSelectedTemplate(null);
                    }}
                    className={`block w-full rounded-md border p-3 text-left transition-colors ${
                      selectedJob?.id === j.id
                        ? "border-primary bg-accent"
                        : "border-border hover:bg-accent/50"
                    }`}
                  >
                    <div className="mb-1 text-sm font-medium leading-tight">
                      {j.name || `Job ${j.id.slice(0, 8)}`}
                    </div>
                    <StatusBadge status={j.status} />
                    <div className="mt-2 text-xs text-muted-foreground">
                      {j.completed_items}/{j.total_items} items
                      {j.failed_items > 0 && (
                        <span className="ml-1 text-status-critical">({j.failed_items} failed)</span>
                      )}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                      {fmtDate(j.created_at)}
                    </div>
                  </button>
                ))}
              </div>
            )
          ) : /* schedules */
          schedules.length === 0 ? (
            <EmptyState
              icon={Calendar}
              title="No Schedules"
              description="Create a schedule to run templates automatically on a recurring basis."
            />
          ) : (
            <div className="space-y-2">
              {schedules.map((s) => (
                <div
                  key={s.id}
                  className="rounded-md border border-border p-3 transition-colors hover:bg-accent/50"
                >
                  <div className="mb-1 text-sm font-medium leading-tight">
                    {s.name || `Schedule ${s.id.slice(0, 8)}`}
                  </div>
                  <StatusBadge status={s.status} />
                  <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-[10px] text-muted-foreground">
                    <span>Freq: {s.frequency}</span>
                    <span>Locs: {s.locations_count}</span>
                    <span>
                      Runs: {s.runs_completed}
                      {s.max_runs ? `/${s.max_runs}` : ""}
                    </span>
                    <span>{s.next_run_at ? `Next: ${fmtDate(s.next_run_at)}` : ""}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Detail Panel */}
        <div className="flex flex-1 flex-col overflow-auto p-6">
          {selectedTemplate ? (
            <TemplateDetail
              template={selectedTemplate}
              jobs={jobs.filter((j) => j.template_id === selectedTemplate.id)}
              fmtDate={fmtDate}
            />
          ) : selectedJob ? (
            <JobDetail job={selectedJob} fmtDate={fmtDate} />
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center text-center">
              <GitBranch className="mb-4 h-12 w-12 text-muted-foreground/30" />
              <p className="mb-2 text-sm font-medium text-muted-foreground">Workflow Builder</p>
              <p className="max-w-md text-xs text-muted-foreground">
                {templates.length === 0
                  ? "No workflow templates found. Run an analysis in the AI Chat workspace, and the system will learn reusable patterns that appear here as templates."
                  : "Select a template to view details, run batch jobs, or create schedules."}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

/* ── Sub-components ── */

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col items-center py-12 text-center">
      <Icon className="mb-3 h-8 w-8 text-muted-foreground/30" />
      <p className="mb-1 text-sm font-medium text-muted-foreground">{title}</p>
      <p className="max-w-[200px] text-xs text-muted-foreground">{description}</p>
    </div>
  );
}

function TemplateDetail({
  template,
  jobs,
  fmtDate,
}: {
  template: WorkflowTemplate;
  jobs: BatchJob[];
  fmtDate: (iso: string) => string;
}) {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold">{template.name}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{template.description}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {template.tags.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs"
            >
              <Tag className="h-3 w-3" />
              {tag}
            </span>
          ))}
          {template.index_type && (
            <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
              {template.index_type.toUpperCase()}
            </span>
          )}
          {template.collection_id && (
            <span className="rounded-md border border-border px-2 py-0.5 font-mono text-[10px]">
              {template.collection_id}
            </span>
          )}
        </div>
      </div>

      {/* Metadata */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Version", value: `v${template.version}` },
          { label: "Runs", value: String(template.use_count) },
          { label: "Created", value: fmtDate(template.created_at) },
          { label: "Updated", value: fmtDate(template.updated_at) },
        ].map((m) => (
          <div key={m.label} className="rounded-md bg-muted/50 p-3">
            <span className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {m.label}
            </span>
            <span className="mt-1 block text-sm font-medium">{m.value}</span>
          </div>
        ))}
      </div>

      {/* Parameters */}
      {template.parameters.length > 0 && (
        <div>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Parameters
          </h3>
          <div className="rounded-md border border-border">
            {template.parameters.map((p, i) => (
              <div
                key={p.name}
                className={`flex items-center justify-between px-4 py-2.5 ${
                  i > 0 ? "border-t border-border" : ""
                }`}
              >
                <div>
                  <span className="text-sm font-medium">{p.name}</span>
                  <span className="ml-2 font-mono text-[10px] text-muted-foreground">{p.type}</span>
                  {p.required && (
                    <span className="ml-1 text-[10px] text-status-critical">required</span>
                  )}
                  {p.description && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{p.description}</p>
                  )}
                </div>
                <span className="font-mono text-xs text-muted-foreground">
                  {p.default_value != null ? String(p.default_value) : "\u2014"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Source Query */}
      {template.source_query && (
        <div>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Source Query
          </h3>
          <p className="rounded-md bg-muted/50 p-3 text-sm leading-relaxed">
            {template.source_query}
          </p>
        </div>
      )}

      {/* Process Graph Preview */}
      <div>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
          Process Graph
        </h3>
        <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {JSON.stringify(template.process_graph, null, 2)}
        </pre>
      </div>

      {/* Recent Jobs for this template */}
      {jobs.length > 0 && (
        <div>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Recent Jobs ({jobs.length})
          </h3>
          <div className="space-y-2">
            {jobs.slice(0, 5).map((j) => (
              <div
                key={j.id}
                className="flex items-center justify-between rounded-md border border-border px-4 py-2.5"
              >
                <div>
                  <span className="text-sm font-medium">{j.name || j.id.slice(0, 8)}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {j.completed_items}/{j.total_items} items
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={j.status} />
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {fmtDate(j.created_at)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function JobDetail({ job, fmtDate }: { job: BatchJob; fmtDate: (iso: string) => string }) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">{job.name || `Batch Job ${job.id.slice(0, 8)}`}</h2>
        <StatusBadge status={job.status} />
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Total Items", value: String(job.total_items) },
          { label: "Completed", value: String(job.completed_items) },
          { label: "Failed", value: String(job.failed_items) },
        ].map((m) => (
          <div key={m.label} className="rounded-md bg-muted/50 p-3">
            <span className="block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {m.label}
            </span>
            <span className="mt-1 block text-sm font-medium">{m.value}</span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <span className="font-mono text-[10px] uppercase text-muted-foreground">Created</span>
          <p className="mt-1">{fmtDate(job.created_at)}</p>
        </div>
        {job.started_at && (
          <div>
            <span className="font-mono text-[10px] uppercase text-muted-foreground">Started</span>
            <p className="mt-1">{fmtDate(job.started_at)}</p>
          </div>
        )}
        {job.completed_at && (
          <div>
            <span className="font-mono text-[10px] uppercase text-muted-foreground">Completed</span>
            <p className="mt-1">{fmtDate(job.completed_at)}</p>
          </div>
        )}
        <div>
          <span className="font-mono text-[10px] uppercase text-muted-foreground">Trigger</span>
          <p className="mt-1 capitalize">{job.trigger_type}</p>
        </div>
      </div>

      <div>
        <span className="font-mono text-[10px] uppercase text-muted-foreground">Template ID</span>
        <p className="mt-1 font-mono text-xs">{job.template_id}</p>
      </div>
    </div>
  );
}

export default WorkflowBuilder;
