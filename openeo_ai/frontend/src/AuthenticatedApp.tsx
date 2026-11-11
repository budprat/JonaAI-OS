/**
 * The authenticated application shell: layout, sidebars, chat, results,
 * projects, and dashboard wiring. Extracted from App.tsx so App.tsx is
 * only the auth gate + route table (TECH-DEBT-HOTSPOTS.md #8, commit 2).
 */
import { useState, useCallback, useEffect } from "react";
import {
  Activity,
  Download,
  Leaf,
  MessageSquare,
  Database,
  FolderOpen,
  Map,
  Wrench,
} from "lucide-react";

// Geo-hub pages
import { Header } from "@/components/layout/Header";
import { PersonaSelector, type Persona } from "@/components/chat/PersonaSelector";
// import { InsightCard, type InsightData } from "@/components/visualizations/InsightCard";
import { Sidebar } from "@/components/layout/Sidebar";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { SettingsDialog } from "@/components/layout/SettingsDialog";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { VisualizationPanel } from "@/components/visualizations/VisualizationPanel";
import { QualityMetricsPanel } from "@/components/sidebar/QualityMetricsPanel";
// WorkflowHistoryPanel moved to Insights tab — keeping import for future use
// import { WorkflowHistoryPanel } from "@/components/sidebar/WorkflowHistoryPanel";
import { ExportPanel } from "@/components/sidebar/ExportPanel";
import { SustainabilityPanel } from "@/components/sidebar/SustainabilityPanel";
import { SavedJobsPanel } from "@/components/sidebar/SavedJobsPanel";
import { ProjectsPanel } from "@/components/sidebar/ProjectsPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { ToastAction } from "@/components/ui/toast";
import { toast } from "@/hooks/use-toast";
import { useJonaChat } from "@/hooks/useJonaChat";
import { useLocalStorage } from "@/hooks/useLocalStorage";
import { useUndoStack } from "@/hooks/useUndoStack";
import { useThemeManager } from "@/hooks/useThemeManager";
import { useBboxSelection } from "@/hooks/useBboxSelection";
import { useIsMobile } from "@/hooks/useIsMobile";
import { Workflow, WorkflowStep, ToolCall, SavedJob, Project, ProjectFile } from "@/types";
import { generateId } from "@/lib/utils";
import { settleWorkflow } from "@/lib/workflow";


export function AuthenticatedApp({
  authUser,
  authToken,
  onLogout,
}: {
  authUser: string | null;
  authToken: string | null;
  onLogout: () => void;
}) {
  // App-specific state (not managed by chat hook)
  const [, setWorkflows] = useLocalStorage<Workflow[]>("openeo-workflows", []);
  const [currentWorkflow, setCurrentWorkflow] = useState<Workflow | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [unassignedFiles, setUnassignedFiles] = useState<ProjectFile[]>([]);
  const [activeProjectId, setActiveProjectId] = useLocalStorage<string | null>(
    "openeo-active-project",
    null
  );
  const [persona, setPersona] = useState<Persona>("analyst");
  const { isDark, handleToggleTheme, handleThemeChange } = useThemeManager();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [savedJobsRefreshTrigger, setSavedJobsRefreshTrigger] = useState(0);
  const isMobile = useIsMobile();
  const [mobileActiveTab, setMobileActiveTab] = useState<"chat" | "map" | "tools">("chat");

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch("/api/projects", {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setProjects(data.projects || []);
        setUnassignedFiles(data.unassigned_files || []);
      }
    } catch {
      /* ignore */
    }
  }, [authToken]);

  useEffect(() => {
    if (authToken) fetchProjects();
  }, [fetchProjects, authToken]);

  // ---- Chat hook (consolidates messages, visualizations, metrics, etc.) ----

  const chat = useJonaChat({
    url: "/ws",
    authToken,
    onToolCall: (toolCall: ToolCall) => {
      // Update current workflow steps
      if (currentWorkflow) {
        setCurrentWorkflow((prev) => {
          if (!prev) return prev;
          const stepIndex = prev.steps.findIndex((s) => s.id === toolCall.id);
          if (stepIndex >= 0) {
            const newSteps = [...prev.steps];
            newSteps[stepIndex] = {
              ...newSteps[stepIndex]!,
              status: toolCall.status,
              result: toolCall.result,
              completedAt: toolCall.status === "completed" ? new Date() : undefined,
            } as WorkflowStep;
            return { ...prev, steps: newSteps };
          } else if (toolCall.status === "running") {
            return {
              ...prev,
              steps: [
                ...prev.steps,
                {
                  id: toolCall.id,
                  name: toolCall.name,
                  status: "running",
                  tool: toolCall.name,
                  arguments: toolCall.arguments,
                  startedAt: new Date(),
                },
              ],
            };
          }
          return prev;
        });
      }
      // Fetch projects on export tools
      const exportTools = [
        "export_styled_geotiff",
        "export_report",
        "export_csv",
        "saved_jobs_save",
        "openeo_create_job",
      ];
      if (toolCall.status === "completed" && exportTools.includes(toolCall.name)) {
        setTimeout(() => fetchProjects(), 300);
      }
    },
    onDone: () => {
      setCurrentWorkflow((prev) => settleWorkflow(prev, "completed"));
      setTimeout(() => fetchProjects(), 500);
    },
    onError: () => {
      setCurrentWorkflow((prev) => settleWorkflow(prev, "error"));
    },
    onSavedJobCreated: () => {
      setSavedJobsRefreshTrigger((prev) => prev + 1);
      // Also refresh projects so PortfolioCommand/dashboard pages see the new result
      setTimeout(() => fetchProjects(), 200);
    },
  });

  // ---- Undo stack ----

  const undoStack = useUndoStack();
  const { selectedBbox, selectedBboxRef, handleBboxChange } = useBboxSelection(undoStack);

  // Keyboard shortcut: Ctrl+Z / Cmd+Z to undo
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.shiftKey) {
        // Don't intercept when user is typing in an input/textarea
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        if (undoStack.undo()) {
          e.preventDefault();
          toast({
            title: "Undone",
            description: "Action reversed",
          });
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [undoStack.undo]);

  // Wrap removeVisualization to push undo action + show toast
  const handleRemoveVisualization = useCallback(
    (vizId: string) => {
      const viz = chat.visualizations.find((v) => v.id === vizId);
      if (!viz) {
        chat.removeVisualization(vizId);
        return;
      }
      // Remove it
      chat.removeVisualization(vizId);
      // Push undo action
      undoStack.push({
        type: "remove_viz",
        label: `Remove ${viz.title || "layer"}`,
        undo: () => chat.restoreVisualization(viz),
      });
      // Show toast with undo button
      toast({
        title: "Layer removed",
        description: viz.title || "Visualization removed",
        action: (
          <ToastAction altText="Undo" onClick={() => chat.restoreVisualization(viz)}>
            Undo
          </ToastAction>
        ),
      });
    },
    [chat.visualizations, chat.removeVisualization, chat.restoreVisualization, undoStack.push]
  );

  // ---- Handlers ----

  const handleSendMessage = useCallback(
    (content: string) => {
      // Create new workflow
      const workflow: Workflow = {
        id: generateId(),
        name: content.slice(0, 50) + (content.length > 50 ? "..." : ""),
        description: content,
        status: "running",
        steps: [],
        createdAt: new Date(),
      };
      setCurrentWorkflow(workflow);
      setWorkflows((prev) => [workflow, ...prev.slice(0, 19)]);

      // Prepend bbox context if selected (use ref to always get latest value)
      const bbox = selectedBboxRef.current;
      const sendContent = bbox
        ? `[Bounding box: west=${bbox.west}, south=${bbox.south}, east=${bbox.east}, north=${bbox.north}] ${content}`
        : content;

      // Send via hook: adds user message (with original content) + sends to backend
      const extra: Record<string, unknown> = {};
      if (activeProjectId) extra.active_project_id = activeProjectId;
      if (persona !== "analyst") extra.persona = persona;
      chat.sendMessage(
        sendContent,
        Object.keys(extra).length > 0 ? extra : undefined,
        bbox ? content : undefined // display original content without bbox prefix
      );
    },
    [chat.sendMessage, setWorkflows, activeProjectId, persona]
  );

  const handleStop = useCallback(() => {
    chat.sendRaw({ type: "stop" });
    setCurrentWorkflow((prev) => settleWorkflow(prev, "stopped"));
  }, [chat.sendRaw]);

  const handleLoadSavedJob = useCallback(
    (job: SavedJob) => {
      chat.sendRaw({ type: "load_saved_job", save_id: job.save_id });
    },
    [chat.sendRaw]
  );

  const handleSelectProject = useCallback(
    (project: Project) => {
      setActiveProjectId(project.id);
    },
    [setActiveProjectId]
  );

  const handleCreateProject = useCallback(
    async (name: string, description: string) => {
      try {
        const res = await fetch("/api/projects", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify({ id: generateId(), name, description }),
        });
        if (res.ok) {
          const project = await res.json();
          setProjects((prev) => [project, ...prev]);
          setActiveProjectId(project.id);
        }
      } catch {
        /* ignore */
      }
    },
    [setActiveProjectId, authToken]
  );

  const handleDeleteProject = useCallback(
    async (id: string) => {
      try {
        await fetch(`/api/projects/${id}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${authToken}` },
        });
        setProjects((prev) => prev.filter((p) => p.id !== id));
        if (activeProjectId === id) setActiveProjectId(null);
        fetchProjects();
      } catch {
        /* ignore */
      }
    },
    [activeProjectId, setActiveProjectId, fetchProjects, authToken]
  );

  const handleRenameProject = useCallback(
    async (id: string, name: string) => {
      try {
        const res = await fetch(`/api/projects/${id}`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify({ name }),
        });
        if (res.ok) {
          const updated = await res.json();
          setProjects((prev) => prev.map((p) => (p.id === id ? updated : p)));
        }
      } catch {
        /* ignore */
      }
    },
    [authToken]
  );

  const handleAssignToProject = useCallback(
    async (saveId: string, projectId: string | null) => {
      try {
        const res = await fetch(`/saved-jobs/${saveId}/project`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify({ project_id: projectId }),
        });
        if (res.ok) fetchProjects();
      } catch {
        /* ignore */
      }
    },
    [fetchProjects, authToken]
  );

  const handleSettings = useCallback(() => {
    setSettingsOpen(true);
  }, []);

  const handleNewChat = useCallback(() => {
    // Archive current conversation before clearing
    chat.conversationHistory.archiveAndStartNew(chat.messages, chat.visualizations);
    chat.clearChat();
    setCurrentWorkflow(null);
    undoStack.clear();
  }, [
    chat.conversationHistory.archiveAndStartNew,
    chat.messages,
    chat.visualizations,
    chat.clearChat,
    undoStack.clear,
  ]);

  const handleHelp = useCallback(() => {
    chat.appendMessage({
      id: generateId(),
      role: "system",
      content: `Welcome to Jona AI!

You can ask questions like:
\u2022 "Show NDVI for Kerala, India during monsoon 2024"
\u2022 "Compare land cover changes in Amazon 2020 vs 2024"
\u2022 "Find cloud-free Sentinel-2 imagery for Paris"
\u2022 "Calculate vegetation health for California"

I can help you with:
- Earth observation data discovery
- Vegetation indices (NDVI, EVI, etc.)
- Change detection analysis
- Terrain analysis
- Time series visualization

Just type your question below!`,
      timestamp: new Date(),
    });
  }, [chat.appendMessage]);

  // Chat panel column (single source — used by the sidebar tab and mobile layout).
  // Keep the overflow-hidden variant: the two former copies had drifted.
  const chatPanelContent = (
    <div className="flex h-full flex-col">
      <div className="flex-shrink-0 border-b bg-muted px-3 py-2">
        <PersonaSelector value={persona} onChange={setPersona} />
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <ChatPanel
          messages={chat.messages}
          uiMessages={chat.uiMessages}
          onSendMessage={handleSendMessage}
          isProcessing={chat.isProcessing}
          isConnected={chat.isConnected}
          connectionState={chat.connectionState}
          onSignIn={onLogout}
          thinkingSteps={chat.thinkingSteps}
          onReconnect={chat.reconnect}
          activeBbox={selectedBbox}
          onStop={handleStop}
          contextSuggestions={chat.contextSuggestions}
          pendingClarification={chat.pendingClarification}
          onClarificationSubmit={chat.submitClarification}
        />
      </div>
    </div>
  );

  // Left sidebar tab (Chat)
  const leftSidebarTabs = [
    {
      id: "chat",
      label: "Chat",
      icon: <MessageSquare className="h-4 w-4" />,
      content: chatPanelContent,
    },
  ];

  // Right sidebar tabs
  const rightSidebarTabs = [
    {
      id: "quality",
      label: "Quality",
      icon: <Activity className="h-4 w-4" />,
      content: <QualityMetricsPanel metrics={chat.qualityMetrics} />,
    },
    {
      id: "projects",
      label: "Projects",
      icon: <FolderOpen className="h-4 w-4" />,
      content: (
        <ProjectsPanel
          projects={projects}
          unassignedFiles={unassignedFiles}
          activeProjectId={activeProjectId}
          onSelectProject={handleSelectProject}
          onCreateProject={handleCreateProject}
          onDeleteProject={handleDeleteProject}
          onRenameProject={handleRenameProject}
          onAssignToProject={handleAssignToProject}
          onRefresh={fetchProjects}
        />
      ),
    },
    {
      id: "insights",
      label: "Insights",
      icon: <Activity className="h-4 w-4" />,
      content: (
        <div className="flex h-full flex-col overflow-auto">
          <div className="border-b px-3 py-2">
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Analysis Insights ({chat.reportSections.length})
            </span>
          </div>
          {chat.reportSections.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-4 text-center">
              <p className="text-xs text-muted-foreground">
                Run an analysis to see interpretations, statistics, and quality assessments here.
              </p>
            </div>
          ) : (
            <div className="space-y-2 p-3">
              {chat.reportSections.map((section) => (
                <div key={section.id} className="rounded-lg border bg-card p-3">
                  <div className="mb-1 flex items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 font-mono text-[9px] uppercase ${
                        section.type === "interpretation"
                          ? "bg-primary/10 text-primary"
                          : section.type === "earth_agent"
                            ? "bg-green-500/10 text-green-600"
                            : section.type === "quality"
                              ? "bg-amber-500/10 text-amber-600"
                              : section.type === "statistics"
                                ? "bg-blue-500/10 text-blue-600"
                                : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {section.type}
                    </span>
                    <span className="truncate text-xs font-medium">{section.title}</span>
                  </div>
                  <div className="prose prose-xs max-h-40 overflow-auto text-xs text-muted-foreground">
                    <pre className="whitespace-pre-wrap break-words font-sans text-[11px] leading-relaxed">
                      {section.content.slice(0, 500)}
                      {section.content.length > 500 ? "..." : ""}
                    </pre>
                  </div>
                  <div className="mt-1 font-mono text-[9px] text-muted-foreground">
                    {section.toolName}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ),
    },
    {
      id: "export",
      label: "Export",
      icon: <Download className="h-4 w-4" />,
      content: (
        <ExportPanel
          messages={chat.messages}
          visualizations={chat.visualizations}
          qualityMetrics={chat.qualityMetrics}
          processGraph={chat.processGraph || undefined}
        />
      ),
    },
    {
      id: "saved",
      label: "Saved",
      icon: <Database className="h-4 w-4" />,
      content: (
        <SavedJobsPanel
          onLoadJob={handleLoadSavedJob}
          onDeleteJob={chat.removeVisualizationBySaveId}
          refreshTrigger={savedJobsRefreshTrigger}
          authToken={authToken}
        />
      ),
    },
    {
      id: "eco",
      label: "Eco",
      icon: <Leaf className="h-4 w-4" />,
      content: <SustainabilityPanel metrics={chat.sustainabilityMetrics} />,
    },
  ];

  // Right sidebar content (reused in both layouts)
  const rightSidebarContent = (
    <Sidebar
      position="right"
      tabs={rightSidebarTabs}
      defaultCollapsed={false}
      isMobile={isMobile}
    />
  );

  if (isMobile) {
    return (
      <TooltipProvider>
        <div className="flex h-screen flex-col bg-background">
          <Header
            isConnected={chat.isConnected}
            isDark={isDark}
            onToggleTheme={handleToggleTheme}
            onHelp={handleHelp}
            onSettings={handleSettings}
            onNewChat={handleNewChat}
            username={authUser}
            onLogout={onLogout}
          />

          <SettingsDialog
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
            onThemeChange={handleThemeChange}
          />

          {/* Mobile: Single active panel fills the screen */}
          <div className="flex flex-1 flex-col overflow-hidden">
            {mobileActiveTab === "chat" && (
              <ErrorBoundary section="Chat" compact>
                <div className="flex flex-1 flex-col overflow-hidden">{chatPanelContent}</div>
              </ErrorBoundary>
            )}
            {mobileActiveTab === "map" && (
              <ErrorBoundary section="Visualization" compact>
                <div className="flex flex-1 flex-col overflow-hidden p-2">
                  {/* {chat.insightData && (
                    <div className="mb-2 flex-shrink-0">
                      <InsightCard data={chat.insightData as unknown as InsightData} />
                    </div>
                  )} */}
                  <VisualizationPanel
                    visualizations={chat.visualizations}
                    className="h-full"
                    onBboxChange={handleBboxChange}
                    bbox={selectedBbox}
                    onRemoveVisualization={handleRemoveVisualization}
                    reportSections={chat.reportSections}
                  />
                </div>
              </ErrorBoundary>
            )}
            {mobileActiveTab === "tools" && (
              <ErrorBoundary section="Tools" compact>
                <div className="flex flex-1 flex-col overflow-y-auto">{rightSidebarContent}</div>
              </ErrorBoundary>
            )}
          </div>

          {/* Mobile bottom tab bar */}
          <nav className="pb-safe flex shrink-0 items-center justify-around border-t border-border bg-background">
            {[
              {
                id: "chat" as const,
                label: "Chat",
                icon: <MessageSquare className="h-5 w-5" />,
              },
              {
                id: "map" as const,
                label: "Map",
                icon: <Map className="h-5 w-5" />,
              },
              {
                id: "tools" as const,
                label: "Tools",
                icon: <Wrench className="h-5 w-5" />,
              },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setMobileActiveTab(tab.id)}
                className={`flex min-h-[56px] flex-1 flex-col items-center justify-center gap-0.5 text-xs font-medium transition-colors ${
                  mobileActiveTab === tab.id
                    ? "text-primary"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
          </nav>
        </div>
        <Toaster />
      </TooltipProvider>
    );
  }

  // Desktop layout
  return (
    <TooltipProvider>
      <div className="flex h-screen flex-col bg-background">
        {/* Skip navigation link for keyboard/screen reader users (WCAG 2.4.1) */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-[9999] focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground focus:shadow-lg"
        >
          Skip to main content
        </a>
        <Header
          isConnected={chat.isConnected}
          isDark={isDark}
          onToggleTheme={handleToggleTheme}
          onHelp={handleHelp}
          onSettings={handleSettings}
          onNewChat={handleNewChat}
          username={authUser}
          onLogout={onLogout}
        />

        <SettingsDialog
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          onThemeChange={handleThemeChange}
        />

        <div className="flex flex-1 overflow-hidden">
          {/* Left Sidebar - Chat */}
          <ErrorBoundary section="Chat" compact>
            <Sidebar position="left" tabs={leftSidebarTabs} defaultCollapsed={false} />
          </ErrorBoundary>

          {/* Main Content - Visualization */}
          <ErrorBoundary section="Visualization">
            <main id="main-content" className="flex flex-1 flex-col overflow-hidden p-3">
              {/* {chat.insightData && (
                <div className="mb-3 flex-shrink-0">
                  <InsightCard data={chat.insightData as unknown as InsightData} />
                </div>
              )} */}
              <VisualizationPanel
                visualizations={chat.visualizations}
                className="h-full"
                onBboxChange={handleBboxChange}
                bbox={selectedBbox}
                onRemoveVisualization={handleRemoveVisualization}
                reportSections={chat.reportSections}
              />
            </main>
          </ErrorBoundary>

          {/* Right Sidebar */}
          <ErrorBoundary section="Tools" compact>
            <Sidebar position="right" tabs={rightSidebarTabs} defaultCollapsed={false} />
          </ErrorBoundary>
        </div>
      </div>
      <Toaster />
    </TooltipProvider>
  );
}
