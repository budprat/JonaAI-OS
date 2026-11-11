import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  MapPinned,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/auth/useAuth";
import {
  useDeliveries,
  useEarthIntelligenceActions,
  useEarthObservations,
  useEarthSignals,
  useSavedResults,
} from "@/hooks/usePlatformData";
import { formatBytes, formatDateTime } from "@/lib/utils";

interface BatchJob {
  id: string;
  template_id: string;
  name: string;
  status: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  trigger_type: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

function statusColor(status: string): string {
  switch (status) {
    case "completed":
      return "bg-status-good";
    case "running":
      return "bg-blue-500";
    case "failed":
      return "bg-status-critical";
    case "cancelled":
      return "bg-muted-foreground";
    default:
      return "bg-status-warn";
  }
}

function statusIcon(status: string) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-3.5 w-3.5 text-status-good" />;
    case "running":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />;
    case "failed":
      return <XCircle className="h-3.5 w-3.5 text-status-critical" />;
    default:
      return <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

const LiveMonitoring: React.FC = () => {
  const { accessToken: authToken } = useAuth();

  const savedResultsHook = useSavedResults(50);
  const observationsHook = useEarthObservations({ limit: 30 });
  const signalsHook = useEarthSignals({ status: "open", limit: 20 });
  const deliveriesHook = useDeliveries({ limit: 20 });
  const earthActions = useEarthIntelligenceActions();

  // Batch jobs are still fetched directly from the REST batch API.
  const [batchJobs, setBatchJobs] = useState<BatchJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const savedResults = savedResultsHook.results;
  const observations = observationsHook.observations;
  const refetchObservations = observationsHook.refetch;
  const openSignals = signalsHook.signals;
  const refetchSignals = signalsHook.refetch;
  const deliveries = deliveriesHook.deliveries;
  const refetchDeliveries = deliveriesHook.refetch;
  const [workerBusy, setWorkerBusy] = useState(false);

  const headers: HeadersInit = authToken ? { Authorization: `Bearer ${authToken}` } : {};

  const fetchData = useCallback(async () => {
    if (!authToken) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/batch/jobs?limit=20", { headers });
      if (response.ok) {
        const data = await response.json();
        setBatchJobs(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load monitoring data");
    } finally {
      setLoading(false);
    }
  }, [authToken]);

  const refreshAll = useCallback(async () => {
    await fetchData();
    refetchObservations();
    refetchSignals();
    refetchDeliveries();
  }, [fetchData, refetchObservations, refetchSignals, refetchDeliveries]);

  const runWorkerTick = useCallback(async () => {
    setWorkerBusy(true);
    setError(null);
    try {
      await earthActions.tickWorker(1);
      await refreshAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to run Earth Intelligence worker");
    } finally {
      setWorkerBusy(false);
    }
  }, [earthActions, refreshAll]);

  useEffect(() => {
    void fetchData();
    // Auto-refresh batch jobs every 30s; saved analyses refresh through useSavedResults.
    const interval = setInterval(() => void fetchData(), 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Derive stats
  const totalResults = savedResults.length;
  const totalBatchJobs = batchJobs.length;
  const runningJobs = batchJobs.filter((j) => j.status === "running").length;
  const failedJobs = batchJobs.filter((j) => j.status === "failed").length;
  const totalSize = savedResults.reduce((sum, r) => sum + r.size_bytes, 0);
  const queuedObservations = observations.filter((o) => o.status === "queued").length;
  const runningObservations = observations.filter((o) => o.status === "running").length;

  if ((loading || savedResultsHook.isLoading) && savedResults.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <span className="font-mono text-xs text-muted-foreground">
            Loading monitoring data...
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Status Bar */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border bg-card px-6 py-3">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 animate-pulse rounded-full bg-green-500" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Live Monitoring
            </span>
          </div>
          {[
            { label: "Analyses", value: String(totalResults) },
            { label: "Batch Jobs", value: String(totalBatchJobs) },
            { label: "Running", value: String(runningJobs) },
            { label: "Failed", value: String(failedJobs) },
            { label: "Queued Obs", value: String(queuedObservations) },
            { label: "Running Obs", value: String(runningObservations) },
            { label: "Open Signals", value: String(openSignals.length) },
            { label: "Deliveries", value: String(deliveries.length) },
            { label: "Archive Size", value: formatBytes(totalSize) },
          ].map((s) => (
            <div key={s.label} className="flex items-center gap-1.5">
              <span className="font-mono text-[10px] uppercase text-muted-foreground">
                {s.label}:
              </span>
              <span className="font-mono text-xs font-medium">{s.value}</span>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" disabled={workerBusy} onClick={() => void runWorkerTick()}>
            {workerBusy ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
            )}
            Worker Tick
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void refreshAll()}>
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Main: Saved Results Grid */}
        <div className="flex-1 overflow-auto p-6">
          <span className="mb-4 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Recent Analyses ({totalResults})
          </span>

          {error && (
            <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
              {error}
            </div>
          )}

          {savedResults.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <MapPinned className="mb-3 h-10 w-10 text-muted-foreground/30" />
              <p className="mb-1 text-sm font-medium text-muted-foreground">No analyses yet</p>
              <p className="max-w-sm text-xs text-muted-foreground">
                Run an analysis in the AI Chat workspace. Saved results will appear here for
                monitoring.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-4">
              {savedResults.map((result) => (
                <a
                  key={result.save_id}
                  href={`/dashboard/analysis/${result.save_id}`}
                  className="block border border-border p-4 transition-colors hover:bg-accent/30"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="line-clamp-1 text-sm font-semibold">{result.title}</h3>
                    <div className="h-2.5 w-2.5 rounded-full bg-status-good" />
                  </div>
                  <div className="mb-2 space-y-1 font-mono text-[10px] text-muted-foreground">
                    <div className="flex justify-between">
                      <span>Colormap</span>
                      <span className="font-medium text-foreground">{result.colormap}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Size</span>
                      <span>{formatBytes(result.size_bytes)}</span>
                    </div>
                    {result.bounds && result.bounds.length > 0 && (
                      <div className="flex justify-between">
                        <span>Bounds</span>
                        <span className="max-w-[120px] truncate">
                          {result.bounds
                            .flat()
                            .map((v) => Number(v).toFixed(2))
                            .join(", ")}
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="font-mono text-[10px] text-muted-foreground">
                    {formatDateTime(result.created_at)}
                  </div>
                </a>
              ))}
            </div>
          )}
        </div>

        {/* Right: Observation + Batch Activity */}
        <div className="flex w-96 flex-shrink-0 flex-col overflow-hidden border-l border-border bg-card">
          <div className="flex-shrink-0 border-b border-border px-4 py-3">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Observation Queue
            </span>
          </div>
          <div className="max-h-[42%] overflow-auto border-b border-border">
            {observations.length === 0 ? (
              <div className="flex flex-col items-center px-4 py-10 text-center">
                <Clock className="mb-2 h-6 w-6 text-muted-foreground/30" />
                <p className="text-xs text-muted-foreground">No persistent observations yet.</p>
              </div>
            ) : (
              observations.slice(0, 12).map((observation) => (
                <div
                  key={observation.id}
                  className="border-b border-border px-4 py-3 transition-colors hover:bg-accent/30"
                >
                  <div className="mb-1 flex items-center gap-2">
                    {statusIcon(observation.status)}
                    <span className="line-clamp-1 text-xs font-medium">
                      {observation.watch_id || `Observation ${observation.id.slice(0, 8)}`}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className={`h-1.5 w-1.5 rounded-full ${statusColor(observation.status)}`} />
                      <span className="font-mono text-[10px] uppercase text-muted-foreground">
                        {observation.status}
                      </span>
                    </div>
                    {observation.openeo_job_id && (
                      <span className="max-w-[130px] truncate font-mono text-[10px] text-muted-foreground">
                        {observation.openeo_job_id}
                      </span>
                    )}
                  </div>
                  {observation.error && (
                    <div className="mt-1 line-clamp-2 text-[10px] text-status-critical">
                      {observation.error}
                    </div>
                  )}
                  <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                    {formatDateTime(observation.created_at)}
                  </div>
                </div>
              ))
            )}
          </div>
          <div className="flex-shrink-0 border-b border-border px-4 py-3">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Delivery State
            </span>
          </div>
          <div className="max-h-[22%] overflow-auto border-b border-border">
            {deliveries.length === 0 ? (
              <div className="px-4 py-6 text-center text-xs text-muted-foreground">
                No signal deliveries queued.
              </div>
            ) : (
              deliveries.slice(0, 8).map((delivery) => (
                <div key={delivery.id} className="border-b border-border px-4 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-medium">{delivery.channel}</span>
                    <span className="font-mono text-[10px] uppercase text-muted-foreground">
                      {delivery.status}
                    </span>
                  </div>
                  <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                    {delivery.target}
                  </div>
                </div>
              ))
            )}
          </div>
          <div className="flex-shrink-0 border-b border-border px-4 py-3">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Batch Job Activity
            </span>
          </div>
          <div className="flex-1 overflow-auto">
            {batchJobs.length === 0 ? (
              <div className="flex flex-col items-center px-4 py-12 text-center">
                <Clock className="mb-2 h-6 w-6 text-muted-foreground/30" />
                <p className="text-xs text-muted-foreground">
                  No batch jobs yet. Create a workflow template and submit a batch job to see
                  activity here.
                </p>
              </div>
            ) : (
              batchJobs.map((job) => (
                <div
                  key={job.id}
                  className="border-b border-border px-4 py-3 transition-colors hover:bg-accent/30"
                >
                  <div className="mb-1 flex items-center gap-2">
                    {statusIcon(job.status)}
                    <span className="line-clamp-1 text-xs font-medium">
                      {job.name || `Job ${job.id.slice(0, 8)}`}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className={`h-1.5 w-1.5 rounded-full ${statusColor(job.status)}`} />
                      <span className="font-mono text-[10px] uppercase text-muted-foreground">
                        {job.status}
                      </span>
                    </div>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {job.completed_items}/{job.total_items} items
                    </span>
                  </div>
                  {job.failed_items > 0 && (
                    <div className="mt-1 flex items-center gap-1 text-[10px] text-status-critical">
                      <AlertTriangle className="h-3 w-3" />
                      {job.failed_items} failed
                    </div>
                  )}
                  <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                    {formatDateTime(job.created_at)}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LiveMonitoring;
