import { useState, useEffect, useCallback, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Database, MapPin, Trash2, RefreshCw, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "@/hooks/use-toast";
import type { SavedJob } from "@/types";
import { cn, formatDateTime, formatBytes } from "@/lib/utils";

interface SavedJobsPanelProps {
  onLoadJob?: (job: SavedJob) => void;
  /** Called when a saved job is deleted (to sync map layers). */
  onDeleteJob?: (saveId: string) => void;
  className?: string;
  /** Increment to trigger a refresh (e.g. after auto-save). */
  refreshTrigger?: number;
  /** Auth token for authenticated API requests. */
  authToken?: string | null;
}

export function SavedJobsPanel({
  onLoadJob,
  onDeleteJob,
  className,
  refreshTrigger = 0,
  authToken,
}: SavedJobsPanelProps) {
  const [jobs, setJobs] = useState<SavedJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: jobs.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 100, // approximate height of a job card
    overscan: 5,
  });

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const headers: HeadersInit = {};
      if (authToken) headers.Authorization = `Bearer ${authToken}`;
      const response = await fetch("/saved-jobs?limit=50", { headers });
      if (!response.ok) throw new Error("Failed to fetch saved jobs");
      const data = await response.json();
      setJobs(data.jobs || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [authToken]);

  useEffect(() => {
    if (authToken) fetchJobs();
  }, [fetchJobs, authToken]);

  // Auto-refresh when a new result is saved
  useEffect(() => {
    if (refreshTrigger > 0 && authToken) {
      fetchJobs();
    }
  }, [refreshTrigger, fetchJobs, authToken]);

  // Prune selection when jobs list changes (e.g. after refresh/delete)
  useEffect(() => {
    setSelectedIds((prev) => {
      const jobIds = new Set(jobs.map((j) => j.save_id));
      const filtered = new Set([...prev].filter((id) => jobIds.has(id)));
      return filtered.size !== prev.size ? filtered : prev;
    });
  }, [jobs]);

  const handleDelete = useCallback(
    async (saveId: string) => {
      try {
        const headers: HeadersInit = {};
        if (authToken) headers.Authorization = `Bearer ${authToken}`;
        const res = await fetch(`/saved-jobs/${saveId}`, { method: "DELETE", headers });
        if (res.ok) {
          setJobs((prev) => prev.filter((j) => j.save_id !== saveId));
          // Only now: a failed delete leaves the result, so its map layer stays too.
          onDeleteJob?.(saveId);
          toast({
            title: "Result deleted",
            description: "The saved result has been removed.",
          });
        } else {
          toast({
            variant: "destructive",
            title: "Delete failed",
            description: `Server returned ${res.status}. Try refreshing.`,
          });
        }
      } catch (err) {
        toast({
          variant: "destructive",
          title: "Delete failed",
          description: err instanceof Error ? err.message : "Network error",
        });
      } finally {
        setConfirmDelete(null);
      }
    },
    [authToken, onDeleteJob],
  );

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      if (prev.size === jobs.length) {
        return new Set();
      }
      return new Set(jobs.map((j) => j.save_id));
    });
  }, [jobs]);

  const handleBulkDelete = useCallback(async () => {
    if (selectedIds.size === 0) return;
    const confirmed = window.confirm(
      `Delete ${selectedIds.size} selected result${selectedIds.size > 1 ? "s" : ""}? This cannot be undone.`,
    );
    if (!confirmed) return;

    setBulkDeleting(true);
    const idsToDelete = [...selectedIds];
    try {
      const deleteHeaders: HeadersInit = {};
      if (authToken) deleteHeaders.Authorization = `Bearer ${authToken}`;
      const results = await Promise.allSettled(
        idsToDelete.map((id) =>
          fetch(`/saved-jobs/${id}`, { method: "DELETE", headers: deleteHeaders })
            .then((res) => ({ id, ok: res.ok })),
        ),
      );
      const deletedIds = results
        .filter((r) => r.status === "fulfilled" && r.value.ok)
        .map((r) => (r as PromiseFulfilledResult<{ id: string; ok: boolean }>).value.id);
      if (deletedIds.length > 0) {
        setJobs((prev) => prev.filter((j) => !deletedIds.includes(j.save_id)));
        for (const id of deletedIds) {
          onDeleteJob?.(id);
        }
      }
      setSelectedIds(new Set());
      const failed = idsToDelete.length - deletedIds.length;
      toast({
        title: `${deletedIds.length} result${deletedIds.length !== 1 ? "s" : ""} deleted${failed > 0 ? `, ${failed} failed` : ""}`,
        description: failed > 0 ? "Some deletions failed. Try refreshing." : "The selected saved results have been removed.",
        variant: failed > 0 ? "destructive" : "default",
      });
    } finally {
      setBulkDeleting(false);
    }
  }, [selectedIds, authToken, onDeleteJob]);

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Database className="h-4 w-4" />
            Saved Results
          </CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={fetchJobs}
            disabled={loading}
            className="h-7 w-7"
            title="Refresh"
            aria-label="Refresh saved jobs list"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-hidden p-4 pt-0">
        {error ? (
          <div
            className="py-8 text-center text-sm text-destructive"
            role="alert"
          >
            {error}
          </div>
        ) : jobs.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            {loading
              ? "Loading..."
              : "No saved results yet. Run an analysis to get started."}
          </div>
        ) : (
          <>
            {/* Select all / bulk action bar */}
            <div className="mb-2 flex items-center justify-between gap-2">
              <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={selectedIds.size === jobs.length && jobs.length > 0}
                  ref={(el) => {
                    if (el) {
                      el.indeterminate =
                        selectedIds.size > 0 && selectedIds.size < jobs.length;
                    }
                  }}
                  onChange={toggleSelectAll}
                  className="h-3.5 w-3.5 rounded border-gray-300 accent-primary cursor-pointer"
                  aria-label="Select all saved jobs"
                />
                Select all
              </label>
              {selectedIds.size > 0 && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={handleBulkDelete}
                  disabled={bulkDeleting}
                  className="h-7 px-2 text-xs"
                  aria-label={`Delete ${selectedIds.size} selected results`}
                >
                  {bulkDeleting ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <Trash2 className="mr-1 h-3 w-3" />
                  )}
                  Delete {selectedIds.size} selected
                </Button>
              )}
            </div>

            {/* Virtualized job list */}
            <div
              ref={scrollContainerRef}
              className="overflow-auto"
              style={{ maxHeight: "calc(100vh - 260px)" }}
              role="list"
              aria-label="Saved jobs list"
            >
              <div
                style={{
                  height: virtualizer.getTotalSize(),
                  width: "100%",
                  position: "relative",
                }}
              >
                {virtualizer.getVirtualItems().map((virtualItem) => {
                  const job = jobs[virtualItem.index]!;
                  // Lost on this server but backed up offsite: loading it restores it first.
                  const needsRestore = job.available === false;
                  return (
                    <div
                      key={virtualItem.key}
                      data-index={virtualItem.index}
                      ref={virtualizer.measureElement}
                      role="listitem"
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${virtualItem.start}px)`,
                      }}
                    >
                      <div
                        className={cn(
                          "mb-2 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50",
                          selectedIds.has(job.save_id) &&
                            "ring-2 ring-primary/30 bg-primary/5",
                        )}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-start gap-2 flex-1 min-w-0">
                            <input
                              type="checkbox"
                              checked={selectedIds.has(job.save_id)}
                              onChange={() => toggleSelect(job.save_id)}
                              className="mt-1 h-3.5 w-3.5 shrink-0 rounded border-gray-300 accent-primary cursor-pointer"
                              aria-label={`Select ${job.title}`}
                              onClick={(e) => e.stopPropagation()}
                            />
                            <div className="flex-1 min-w-0">
                              <h4 className="truncate text-sm font-medium">
                                {job.title}
                              </h4>
                              <p className="mt-0.5 text-xs text-muted-foreground">
                                {formatBytes(job.size_bytes)} &middot;{" "}
                                {job.save_id}
                              </p>
                            </div>
                          </div>
                          <Badge
                            variant="outline"
                            className="shrink-0 text-[10px]"
                            title={
                              needsRestore
                                ? "Not on this server. Loading it restores it from the offsite copy."
                                : undefined
                            }
                          >
                            {needsRestore ? "Backed up" : "Saved"}
                          </Badge>
                        </div>

                        <div className="mt-2 flex items-center justify-between">
                          <span className="text-xs text-muted-foreground">
                            {formatDateTime(job.created_at)}
                          </span>

                          <div className="flex gap-1">
                            {onLoadJob && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => onLoadJob(job)}
                                className="h-7 px-2 text-xs"
                                aria-label={
                                  needsRestore
                                    ? `Restore ${job.title} and load it on the map`
                                    : `Load ${job.title} on map`
                                }
                              >
                                <MapPin className="mr-1 h-3 w-3" />
                                {needsRestore ? "Restore & load" : "Load on Map"}
                              </Button>
                            )}
                            {confirmDelete === job.save_id ? (
                              <>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => setConfirmDelete(null)}
                                  className="h-7 px-2 text-xs"
                                  aria-label="Cancel delete"
                                >
                                  Cancel
                                </Button>
                                <Button
                                  variant="destructive"
                                  size="sm"
                                  onClick={() => handleDelete(job.save_id)}
                                  className="h-7 px-2 text-xs"
                                  aria-label={`Confirm delete ${job.title}`}
                                >
                                  Confirm
                                </Button>
                              </>
                            ) : (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setConfirmDelete(job.save_id)}
                                className="h-7 px-2 text-xs text-destructive hover:text-destructive"
                                aria-label={`Delete ${job.title}`}
                              >
                                <Trash2 className="h-3 w-3" />
                              </Button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
