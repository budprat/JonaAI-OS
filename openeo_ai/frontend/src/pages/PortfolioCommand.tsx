import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  Database,
  FolderOpen,
  HardDrive,
  Inbox,
  RefreshCw,
} from "lucide-react";
import { useProjects, useSavedResults } from "@/hooks/usePlatformData";
import { useAuth } from "@/components/auth/useAuth";
import { formatBytes, formatDateTime } from "@/lib/utils";
import type { Project, ProjectFile } from "@/types";

type ViewState = "loading" | "data" | "error" | "empty";
type ProjectFilter = "all" | "with-analyses" | "empty";
type ActivityFilter = "all" | "7d" | "30d";

const PROJECT_FILTERS: Array<{ id: ProjectFilter; label: string }> = [
  { id: "all", label: "All Projects" },
  { id: "with-analyses", label: "With Analyses" },
  { id: "empty", label: "Empty Projects" },
];

const ACTIVITY_FILTERS: Array<{ id: ActivityFilter; label: string }> = [
  { id: "all", label: "Any Time" },
  { id: "7d", label: "Updated 7 Days" },
  { id: "30d", label: "Updated 30 Days" },
];

export default function PortfolioCommand() {
  const navigate = useNavigate();
  const { accessToken: authToken } = useAuth();

  const projectsHook = useProjects();
  const savedResultsHook = useSavedResults(100);

  const [projectFilter, setProjectFilter] = useState<ProjectFilter>("all");
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState("Failed to load portfolio data.");

  const projects = projectsHook.projects as unknown as Project[];
  const savedJobs = savedResultsHook.results;
  const unassignedFiles = projectsHook.unassignedFiles as unknown as ProjectFile[];

  const fetchPortfolio = useCallback(() => {
    if (!authToken) {
      setErrorMessage("Geo Hub portfolio requires an authenticated session.");
      setViewState("error");
      return;
    }
    setViewState("loading");
    setErrorMessage("Failed to load portfolio data.");
    projectsHook.refetch();
    savedResultsHook.refetch();
  }, [authToken, projectsHook, savedResultsHook]);

  // Update viewState when live REST data arrives
  useEffect(() => {
    if (!authToken) {
      setErrorMessage("Geo Hub portfolio requires an authenticated session.");
      setViewState("error");
      return;
    }
    if (projectsHook.error || savedResultsHook.error) {
      setErrorMessage(projectsHook.error || savedResultsHook.error || "Failed to load portfolio data.");
      setViewState("error");
      return;
    }
    if (!projectsHook.isLoading && !savedResultsHook.isLoading) {
      const hasData =
        projectsHook.projects.length > 0 ||
        projectsHook.unassignedFiles.length > 0 ||
        savedResultsHook.results.length > 0;
      setViewState(hasData ? "data" : "empty");
    }
  }, [
    authToken,
    projectsHook.error,
    projectsHook.isLoading,
    projectsHook.projects.length,
    projectsHook.unassignedFiles.length,
    savedResultsHook.error,
    savedResultsHook.isLoading,
    savedResultsHook.results.length,
  ]);

  const filteredProjects = useMemo(() => {
    const now = Date.now();
    const horizonMs =
      activityFilter === "7d"
        ? 7 * 24 * 60 * 60 * 1000
        : activityFilter === "30d"
          ? 30 * 24 * 60 * 60 * 1000
          : null;

    return [...projects]
      .filter((project) => {
        if (projectFilter === "with-analyses") {
          return project.analysis_count > 0;
        }
        if (projectFilter === "empty") {
          return project.analysis_count === 0;
        }
        return true;
      })
      .filter((project) => {
        if (!horizonMs) {
          return true;
        }
        return now - new Date(project.updated_at).getTime() <= horizonMs;
      })
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [activityFilter, projectFilter, projects]);

  const assignedAnalyses = useMemo(
    () => projects.reduce((sum, project) => sum + (project.files?.length || 0), 0),
    [projects]
  );

  const totalStorageBytes = useMemo(
    () => savedJobs.reduce((sum, job) => sum + (job.size_bytes || 0), 0),
    [savedJobs]
  );

  const recentAnalyses = useMemo(
    () =>
      [...savedJobs]
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
        .slice(0, 6),
    [savedJobs]
  );

  return (
    <div className="flex h-full">
      <aside className="w-60 flex-shrink-0 border-r border-border bg-card p-4">
        <div className="mb-6 flex items-center gap-2">
          <FolderOpen className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Filters
          </span>
        </div>

        <div className="mb-6">
          <label className="mb-2 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Projects
          </label>
          {PROJECT_FILTERS.map((filter) => (
            <button
              key={filter.id}
              onClick={() => setProjectFilter(filter.id)}
              className={`block w-full rounded-md px-3 py-1.5 text-left text-sm transition-colors ${
                projectFilter === filter.id
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-accent"
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <div>
          <label className="mb-2 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Activity Window
          </label>
          {ACTIVITY_FILTERS.map((filter) => (
            <button
              key={filter.id}
              onClick={() => setActivityFilter(filter.id)}
              className={`block w-full rounded-md px-3 py-1.5 text-left text-sm transition-colors ${
                activityFilter === filter.id
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-accent"
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </aside>

      <main className="screen-enter flex-1 overflow-auto">
        <div className="border-b border-border bg-card/60 px-6 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Database className="h-4 w-4 text-primary" aria-hidden="true" />
              <div>
                <div className="text-sm font-medium">Live Portfolio</div>
                <div className="text-sm text-muted-foreground">
                  This route is backed by your real projects and saved analyses. Other Geo Hub
                  routes remain preview-only until their live integrations land.
                </div>
              </div>
            </div>
            <button
              onClick={() => navigate("/app")}
              className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm transition-colors hover:bg-accent"
            >
              Continue in AI Workspace
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="border-b border-border px-6 py-5">
          <div className="grid gap-4 md:grid-cols-4">
            <SummaryCard
              label="Projects"
              value={projects.length}
              icon={<FolderOpen className="h-5 w-5 text-primary" aria-hidden="true" />}
            />
            <SummaryCard
              label="Saved Analyses"
              value={savedJobs.length}
              icon={<Database className="h-5 w-5 text-primary" aria-hidden="true" />}
            />
            <SummaryCard
              label="Assigned vs Unassigned"
              value={`${assignedAnalyses}/${unassignedFiles.length}`}
              icon={<Inbox className="h-5 w-5 text-primary" aria-hidden="true" />}
            />
            <SummaryCard
              label="Stored Volume"
              value={formatBytes(totalStorageBytes)}
              icon={<HardDrive className="h-5 w-5 text-primary" aria-hidden="true" />}
            />
          </div>
        </div>

        {viewState === "loading" && (
          <div className="px-6 py-4">
            <div className="border border-border">
              <div className="border-b border-border bg-card px-4 py-2.5">
                <div className="h-3 w-full animate-pulse rounded-md bg-muted" />
              </div>
              {[1, 2, 3, 4, 5].map((i) => (
                <div
                  key={i}
                  className="flex items-center gap-6 border-b border-border px-4 py-3.5 last:border-0"
                >
                  <div className="h-4 w-40 animate-pulse rounded-md bg-muted" />
                  <div className="h-4 w-24 animate-pulse rounded-md bg-muted" />
                  <div className="h-4 w-20 animate-pulse rounded-md bg-muted" />
                  <div className="h-4 w-28 animate-pulse rounded-md bg-muted" />
                  <div className="ml-auto h-8 w-28 animate-pulse rounded-md bg-muted" />
                </div>
              ))}
            </div>
          </div>
        )}

        {viewState === "error" && (
          <div className="px-6 py-4">
            <div className="border border-border bg-card p-6">
              <div className="flex items-center gap-3">
                <AlertTriangle
                  className="h-5 w-5 flex-shrink-0 text-status-critical"
                  aria-hidden="true"
                />
                <div>
                  <div className="text-sm font-medium">Portfolio sync failed.</div>
                  <div className="mt-0.5 text-sm text-muted-foreground">
                    {errorMessage}{" "}
                    <button
                      onClick={() => void fetchPortfolio()}
                      className="ml-1 font-medium text-primary underline"
                    >
                      Retry
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {viewState === "empty" && (
          <div className="px-6 py-4">
            <div className="border border-border p-12 text-center">
              <div className="mb-3 text-sm text-muted-foreground">
                No projects or saved analyses yet.
              </div>
              <div className="text-sm text-muted-foreground">
                Run an analysis in the AI workspace to start building your portfolio metadata.
              </div>
              <button
                onClick={() => navigate("/app")}
                className="mt-4 inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm transition-colors hover:bg-accent"
              >
                Open AI Workspace
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>
        )}

        {viewState === "data" && (
          <div className="px-6 py-4">
            <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
              <section className="rounded-lg border bg-card">
                <div className="flex items-center justify-between border-b px-4 py-3">
                  <div>
                    <h2 className="text-sm font-semibold">Projects</h2>
                    <p className="text-xs text-muted-foreground">
                      Backed by `/api/projects` and filtered by your current portfolio state.
                    </p>
                  </div>
                  <button
                    onClick={() => void fetchPortfolio()}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-xs transition-colors hover:bg-accent"
                  >
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                    Refresh
                  </button>
                </div>

                {filteredProjects.length === 0 ? (
                  <div className="p-6 text-sm text-muted-foreground">
                    No projects matched the selected filters.
                  </div>
                ) : (
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="px-4 py-3 text-left font-mono text-xs uppercase tracking-wider text-muted-foreground">
                          Project
                        </th>
                        <th className="px-4 py-3 text-right font-mono text-xs uppercase tracking-wider text-muted-foreground">
                          Analyses
                        </th>
                        <th className="px-4 py-3 text-right font-mono text-xs uppercase tracking-wider text-muted-foreground">
                          Storage
                        </th>
                        <th className="px-4 py-3 text-left font-mono text-xs uppercase tracking-wider text-muted-foreground">
                          Updated
                        </th>
                        <th className="px-4 py-3 text-right font-mono text-xs uppercase tracking-wider text-muted-foreground">
                          Action
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredProjects.map((project) => {
                        const projectBytes = (project.files || []).reduce(
                          (sum, file) => sum + (file.size_bytes || 0),
                          0
                        );

                        return (
                          <tr key={project.id} className="border-b border-border last:border-b-0">
                            <td className="px-4 py-4 align-top">
                              <div className="font-medium">{project.name}</div>
                              <div className="mt-1 text-sm text-muted-foreground">
                                {project.description || "No description yet."}
                              </div>
                              <div className="mt-2 flex flex-wrap gap-2">
                                <span className="rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                                  {project.analysis_count > 0 ? "Active" : "Empty"}
                                </span>
                                <span className="rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                                  ID {project.id}
                                </span>
                              </div>
                            </td>
                            <td className="px-4 py-4 text-right font-mono text-sm">
                              {project.analysis_count}
                            </td>
                            <td className="px-4 py-4 text-right font-mono text-sm">
                              {formatBytes(projectBytes)}
                            </td>
                            <td className="px-4 py-4 text-sm text-muted-foreground">
                              {formatDateTime(project.updated_at)}
                            </td>
                            <td className="px-4 py-4 text-right">
                              <div className="flex items-center justify-end gap-2">
                                <button
                                  onClick={() => navigate(`/dashboard/project/${project.id}`)}
                                  className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
                                >
                                  View Project
                                  <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </section>

              <div className="space-y-4">
                <section className="rounded-lg border bg-card">
                  <div className="border-b px-4 py-3">
                    <h2 className="text-sm font-semibold">Unassigned Analyses</h2>
                    <p className="text-xs text-muted-foreground">
                      Saved outputs that have not been attached to a project yet.
                    </p>
                  </div>
                  <div className="p-4">
                    {unassignedFiles.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        All saved analyses are assigned.
                      </p>
                    ) : (
                      <div className="space-y-3">
                        {unassignedFiles.slice(0, 5).map((file) => (
                          <div key={file.save_id} className="rounded-md border border-border p-3">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="font-medium">{file.title}</div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                  {formatDateTime(file.created_at)} · {formatBytes(file.size_bytes)}
                                </div>
                              </div>
                              <button
                                onClick={() => navigate(`/dashboard/analysis/${file.save_id}`)}
                                className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-sm transition-colors hover:bg-accent"
                              >
                                Open Briefing
                                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </section>

                <section className="rounded-lg border bg-card">
                  <div className="border-b px-4 py-3">
                    <h2 className="text-sm font-semibold">Recent Saved Analyses</h2>
                    <p className="text-xs text-muted-foreground">
                      Latest archive entries from `/saved-jobs`.
                    </p>
                  </div>
                  <div className="p-4">
                    {recentAnalyses.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No saved analyses available.</p>
                    ) : (
                      <div className="space-y-3">
                        {recentAnalyses.map((job) => (
                          <div key={job.save_id} className="rounded-md border border-border p-3">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="font-medium">{job.title}</div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                  {formatDateTime(job.created_at)}
                                </div>
                              </div>
                              <span className="font-mono text-xs text-muted-foreground">
                                {formatBytes(job.size_bytes)}
                              </span>
                            </div>
                            <div className="mt-3">
                              <button
                                onClick={() => navigate(`/dashboard/analysis/${job.save_id}`)}
                                className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-sm transition-colors hover:bg-accent"
                              >
                                Open Briefing
                                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </section>

                <section className="rounded-lg border bg-card">
                  <div className="border-b px-4 py-3">
                    <h2 className="text-sm font-semibold">Integration Status</h2>
                  </div>
                  <div className="space-y-3 p-4 text-sm text-muted-foreground">
                    <div className="flex items-start gap-2">
                      <Database className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                      <span>
                        Portfolio and saved-analysis briefing routes are live-backed by project and
                        archive metadata.
                      </span>
                    </div>
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
                      <span>
                        Monitoring, satellites, workflows, ROI, scenarios, and intervention pages
                        are still preview surfaces with sample data.
                      </span>
                    </div>
                  </div>
                </section>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
}) {
  return (
    <div>
      <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="mt-2 flex items-center gap-3">
        {icon}
        <span className="font-mono text-3xl font-semibold">{value}</span>
      </div>
    </div>
  );
}
