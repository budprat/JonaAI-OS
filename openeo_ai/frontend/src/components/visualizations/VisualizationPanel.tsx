import React, { useState, useCallback, useMemo, lazy, Suspense } from "react";
import { Map, FileText, Table2, GitBranch, Layers, X } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Visualization,
  ChartVisualization,
  ComparisonVisualization,
  MapVisualization,
  MultiPanelVisualization,
  DashboardVisualization,
  TimelapseVisualization,
  ProcessGraphVisualization,
  BBox,
  ClassificationVisualization,
} from "@/types";
// Lazy-loaded heavy visualization components for code splitting
const MapViewer = lazy(() => import("./MapViewer").then((m) => ({ default: m.MapViewer })));
const ComparisonMapViewer = lazy(() =>
  import("./ComparisonMapViewer").then((m) => ({
    default: m.ComparisonMapViewer,
  }))
);
const MultiPanelMapViewer = lazy(() =>
  import("./MultiPanelMapViewer").then((m) => ({
    default: m.MultiPanelMapViewer,
  }))
);
const TimelapseViewer = lazy(() =>
  import("./TimelapseViewer").then((m) => ({ default: m.TimelapseViewer }))
);
const DashboardViewer = lazy(() =>
  import("./DashboardViewer").then((m) => ({ default: m.DashboardViewer }))
);
const ChartViewer = lazy(() => import("./ChartViewer").then((m) => ({ default: m.ChartViewer })));

/** Loading fallback for lazy-loaded visualization components */
function VizLoadingFallback() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-2">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <span className="text-xs text-muted-foreground">Loading viewer...</span>
      </div>
    </div>
  );
}
import { TimelineSlider } from "./map-tools/TimelineSlider";
import { ReportPanel } from "./ReportPanel";
import { DataTable } from "./DataTable";
import { ProcessGraphViewer } from "./ProcessGraphViewer";
import { cn } from "@/lib/utils";

interface VisualizationPanelProps {
  visualizations: Visualization[];
  className?: string;
  onBboxChange?: (bbox: BBox | null) => void;
  bbox?: BBox | null;
  /** Called when a layer is removed via the map layer panel. */
  onRemoveVisualization?: (vizId: string) => void;
  /** Report sections from analysis tools (replaces Stats tab). */
  reportSections?: import("@/types").ReportSection[];
}

/** Tracks which two layers are being compared side-by-side. */
interface CompareMode {
  layerA: string;
  layerB: string;
}

export function VisualizationPanel({
  visualizations,
  className,
  onBboxChange,
  bbox,
  onRemoveVisualization,
  reportSections = [],
}: VisualizationPanelProps) {
  const [activeTab, setActiveTab] = useState("map");
  const [compareMode, setCompareMode] = useState<CompareMode | null>(null);
  const [activeTemporalLayerId, setActiveTemporalLayerId] = useState<string | null>(null);

  const maps = visualizations.filter(
    (v) =>
      v.type === "map" ||
      v.type === "comparison" ||
      v.type === "multi_panel" ||
      v.type === "timelapse" ||
      v.type === "dashboard"
  );
  const charts = visualizations.filter((v) => v.type === "chart");
  const tables = visualizations.filter((v) => v.type === "table");
  const processGraphs = visualizations.filter((v) => v.type === "process_graph");
  const classifications = visualizations.filter((v) => v.type === "classification");

  // Detect temporal layers: map layers whose titles contain date-like patterns
  const temporalLayers = useMemo(() => {
    const datePattern =
      /\d{4}[-/]\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}/i;
    return maps
      .filter((v) => v.type === "map" && v.title && datePattern.test(v.title))
      .map((v) => ({ id: v.id, timestamp: v.title || "", title: v.title }));
  }, [maps]);

  // Auto-select latest temporal layer when new ones arrive
  React.useEffect(() => {
    if (temporalLayers.length >= 2 && !activeTemporalLayerId) {
      setActiveTemporalLayerId(temporalLayers[temporalLayers.length - 1]!.id);
    }
  }, [temporalLayers, activeTemporalLayerId]);

  const hasTimeline = temporalLayers.length >= 2;

  const latestMap = maps[maps.length - 1];
  const latestProcessGraph = processGraphs[processGraphs.length - 1];

  const prevCounts = React.useRef({
    maps: maps.length,
    charts: charts.length,
    tables: tables.length,
    processGraphs: processGraphs.length,
  });
  React.useEffect(() => {
    const prev = prevCounts.current;
    // Always switch to map when new map data arrives (highest priority)
    if (maps.length > prev.maps) {
      setActiveTab("map");
    } else if (activeTab !== "map") {
      // Only auto-switch to other tabs if user is NOT viewing the map
      // This prevents the map from "disappearing" when a job starts
      if (charts.length > prev.charts) {
        setActiveTab("visualization");
      } else if (tables.length > prev.tables) {
        setActiveTab("table");
      } else if (processGraphs.length > prev.processGraphs) {
        setActiveTab("process");
      }
    }
    prevCounts.current = {
      maps: maps.length,
      charts: charts.length,
      tables: tables.length,
      processGraphs: processGraphs.length,
    };
  }, [maps.length, charts.length, tables.length, processGraphs.length, activeTab]);

  const hasAnyData = visualizations.length > 0;

  // Build comparison data from two selected layer IDs
  const handleCompare = useCallback((layerA: string, layerB: string) => {
    setCompareMode({ layerA, layerB });
  }, []);

  const handleExitCompare = useCallback(() => {
    setCompareMode(null);
  }, []);

  // Build ComparisonVisualization data from compareMode layer IDs
  const comparisonData = useMemo((): ComparisonVisualization | null => {
    if (!compareMode) return null;

    const mapVizs = maps.filter((v) => v.type === "map");
    const layerA = mapVizs.find((v) => v.id === compareMode.layerA);
    const layerB = mapVizs.find((v) => v.id === compareMode.layerB);

    if (!layerA || !layerB) return null;

    return {
      before: layerA.data as MapVisualization,
      after: layerB.data as MapVisualization,
      labels: {
        before: layerA.title || compareMode.layerA,
        after: layerB.title || compareMode.layerB,
      },
    };
  }, [compareMode, maps]);

  return (
    <Card className={cn("flex h-full flex-col", className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Results</CardTitle>
          <div className="hidden gap-1 sm:flex">
            <Badge variant="default">Deep Analysis</Badge>
            <Badge variant="secondary">GeoTIFF + JSON</Badge>
          </div>
        </div>
      </CardHeader>

      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex flex-1 flex-col overflow-hidden"
      >
        <TabsList
          className="mx-4 flex w-auto gap-1 overflow-x-auto sm:grid sm:grid-cols-5"
          aria-label="Visualization tabs"
        >
          <TabsTrigger value="visualization" className="flex shrink-0 items-center gap-1 text-xs">
            <Layers className="h-3 w-3" />
            <span className="hidden sm:inline">Charts</span>
            <span className="sm:hidden">Charts</span>
            {charts.length > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
                {charts.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="map" className="flex shrink-0 items-center gap-1 text-xs">
            <Map className="h-3 w-3" />
            Map
          </TabsTrigger>
          <TabsTrigger value="table" className="flex shrink-0 items-center gap-1 text-xs">
            <Table2 className="h-3 w-3" />
            <span className="hidden sm:inline">Data Table</span>
            <span className="sm:hidden">Table</span>
          </TabsTrigger>
          <TabsTrigger value="report" className="flex shrink-0 items-center gap-1 text-xs">
            <FileText className="h-3 w-3" />
            Report
            {reportSections.length > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
                {reportSections.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="process" className="flex shrink-0 items-center gap-1 text-xs">
            <GitBranch className="h-3 w-3" />
            <span className="hidden sm:inline">Process Graph</span>
            <span className="sm:hidden">Graph</span>
            {latestProcessGraph && (
              <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
                {(latestProcessGraph.data as ProcessGraphVisualization).nodes.length}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        <CardContent className="flex flex-1 flex-col overflow-hidden p-4">
          <TabsContent value="visualization" className="mt-0 flex-1 overflow-auto">
            <Suspense fallback={<VizLoadingFallback />}>
              {charts.length > 0 ? (
                <div className="flex flex-col gap-4">
                  {charts.map((chart, idx) => {
                    const data = chart.data as ChartVisualization;
                    // Compute inline stats for each chart's series
                    const seriesStats = data.series?.map((s) => {
                      const vals = s.values.filter((v: number) => !isNaN(v));
                      if (vals.length === 0) return null;
                      const min = Math.min(...vals);
                      const max = Math.max(...vals);
                      const mean = vals.reduce((a: number, b: number) => a + b, 0) / vals.length;
                      return { name: s.name, min, max, mean, count: vals.length };
                    });
                    return (
                      <div key={chart.id ?? idx} className="rounded-lg border bg-card">
                        <div className="border-b px-3 py-2">
                          <h4 className="text-sm font-medium">
                            {chart.title || `Chart ${idx + 1}`}
                          </h4>
                        </div>
                        <div className="h-[280px] p-2">
                          <ChartViewer data={data} className="h-full" />
                        </div>
                        {seriesStats && seriesStats.some(Boolean) && (
                          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t px-3 py-2 text-[11px] text-muted-foreground">
                            {seriesStats.filter(Boolean).map((s) => (
                              <span key={s!.name} className="tabular-nums">
                                <span className="font-medium text-foreground/70">{s!.name}:</span>{" "}
                                {s!.min.toFixed(3)} ~ {s!.max.toFixed(3)} (avg {s!.mean.toFixed(3)})
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : hasAnyData ? (
                <EmptyState message="No charts yet — run analysis to see plots" />
              ) : (
                <LoadingSkeleton type="chart" />
              )}
            </Suspense>
          </TabsContent>

          <TabsContent value="map" className="relative mt-0 flex-1 overflow-hidden">
            <Suspense fallback={<VizLoadingFallback />}>
              <div className="absolute inset-0">
                {/* User-initiated comparison mode from layer panel */}
                {compareMode && comparisonData ? (
                  <div className="relative h-full w-full">
                    <ComparisonMapViewer data={comparisonData} className="h-full w-full" />
                    {/* Exit comparison overlay button */}
                    <button
                      onClick={handleExitCompare}
                      className="absolute right-3 top-3 z-[1001] flex items-center gap-1.5 rounded-md border border-black/10 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
                      aria-label="Exit comparison mode"
                    >
                      <X size={13} />
                      Exit Comparison
                    </button>
                  </div>
                ) : latestMap?.type === "dashboard" ? (
                  <DashboardViewer
                    data={latestMap.data as DashboardVisualization}
                    title={latestMap.title}
                    className="h-full w-full"
                  />
                ) : latestMap?.type === "timelapse" ? (
                  <TimelapseViewer
                    data={latestMap.data as TimelapseVisualization}
                    title={latestMap.title}
                    className="h-full w-full"
                  />
                ) : latestMap?.type === "multi_panel" ? (
                  <MultiPanelMapViewer
                    data={latestMap.data as MultiPanelVisualization}
                    title={latestMap.title}
                    className="h-full w-full"
                  />
                ) : latestMap?.type === "comparison" ? (
                  <div className="relative h-full w-full">
                    <ComparisonMapViewer
                      data={latestMap.data as ComparisonVisualization}
                      className="h-full w-full"
                    />
                    {onRemoveVisualization && (
                      <button
                        onClick={() => onRemoveVisualization(latestMap.id)}
                        className="absolute right-3 top-3 z-[1001] flex items-center gap-1.5 rounded-md border border-black/10 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
                        aria-label="Exit comparison mode"
                      >
                        <X size={13} />
                        Exit Comparison
                      </button>
                    )}
                  </div>
                ) : (
                  <MapViewer
                    data={latestMap?.data as MapVisualization | undefined}
                    title={latestMap?.title}
                    saveId={latestMap?.saveId}
                    layers={
                      maps.filter((v) => v.type === "map").length >= 1
                        ? maps
                            .filter((v) => v.type === "map")
                            .map((v) => ({
                              id: v.id,
                              data: v.data as MapVisualization,
                              title: v.title,
                            }))
                        : undefined
                    }
                    className="h-full w-full"
                    onBboxChange={onBboxChange}
                    bbox={bbox}
                    onRemoveLayer={onRemoveVisualization}
                    onCompare={handleCompare}
                  />
                )}
              </div>
              {/* Timeline slider for temporal layers */}
              {hasTimeline && activeTemporalLayerId && (
                <TimelineSlider
                  layers={temporalLayers}
                  activeLayerId={activeTemporalLayerId}
                  onLayerChange={setActiveTemporalLayerId}
                />
              )}
            </Suspense>
          </TabsContent>

          <TabsContent value="table" className="mt-0 flex-1 overflow-auto">
            {tables.length > 0 ? (
              tables.map((table, idx) => (
                <div key={table.id ?? idx} className="mb-4">
                  <h4 className="mb-1 text-sm font-medium">{table.title || `Table ${idx + 1}`}</h4>
                  <DataTable visualization={table} />
                </div>
              ))
            ) : hasAnyData ? (
              <EmptyState message="No table data available yet" />
            ) : (
              <LoadingSkeleton type="table" />
            )}
            {classifications.map((cls) => {
              const clsData = cls.data as ClassificationVisualization;
              return (
                <Card key={cls.id} className="mb-2">
                  <CardContent className="p-3">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-sm font-semibold">
                        {clsData.label}
                      </Badge>
                      {clsData.confidence > 0 && (
                        <span className="text-xs text-muted-foreground">
                          {(clsData.confidence * 100).toFixed(0)}% confidence
                        </span>
                      )}
                    </div>
                    {clsData.categories && clsData.categories.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {clsData.categories.map((cat, i) => (
                          <div key={i} className="flex items-center gap-2 text-xs">
                            <div
                              className="h-2 w-2 rounded-full"
                              style={{ backgroundColor: cat.color || "#8b5cf6" }}
                            />
                            <span>{cat.name}</span>
                            <span className="text-muted-foreground">{cat.value}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </TabsContent>

          <TabsContent value="report" className="mt-0 flex-1 overflow-auto">
            <ReportPanel sections={reportSections} />
          </TabsContent>

          <TabsContent value="process" className="mt-0 flex-1 overflow-auto">
            {latestProcessGraph ? (
              <ProcessGraphViewer data={latestProcessGraph.data as ProcessGraphVisualization} />
            ) : hasAnyData ? (
              <EmptyState message="No process graph available yet" />
            ) : (
              <LoadingSkeleton type="graph" />
            )}
          </TabsContent>
        </CardContent>
      </Tabs>
    </Card>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <p>{message}</p>
    </div>
  );
}

// Skeleton loading placeholders
function SkeletonBlock({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={cn("animate-pulse rounded bg-muted", className)} style={style} />;
}

function LoadingSkeleton({ type }: { type: "map" | "chart" | "table" | "stats" | "graph" }) {
  if (type === "map") {
    return (
      <div className="flex h-full flex-col gap-3 p-4">
        <SkeletonBlock className="h-6 w-48" />
        <SkeletonBlock className="flex-1 rounded-lg" />
        <div className="flex gap-2">
          <SkeletonBlock className="h-8 w-24" />
          <SkeletonBlock className="h-8 w-24" />
          <SkeletonBlock className="h-8 w-24" />
        </div>
      </div>
    );
  }

  if (type === "chart") {
    return (
      <div className="flex h-full flex-col gap-3 p-4">
        <SkeletonBlock className="h-6 w-40" />
        <div className="flex flex-1 items-end gap-2 pb-8">
          {[40, 65, 50, 80, 55, 70, 45, 60, 75, 50].map((h, i) => (
            <SkeletonBlock key={i} className="flex-1" style={{ height: `${h}%` }} />
          ))}
        </div>
        <SkeletonBlock className="h-4 w-full" />
      </div>
    );
  }

  if (type === "table") {
    return (
      <div className="flex flex-col gap-2 p-4">
        <div className="flex gap-4">
          <SkeletonBlock className="h-8 flex-1" />
          <SkeletonBlock className="h-8 flex-1" />
          <SkeletonBlock className="h-8 flex-1" />
          <SkeletonBlock className="h-8 flex-1" />
        </div>
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex gap-4">
            <SkeletonBlock className="h-6 flex-1" />
            <SkeletonBlock className="h-6 flex-1" />
            <SkeletonBlock className="h-6 flex-1" />
            <SkeletonBlock className="h-6 flex-1" />
          </div>
        ))}
      </div>
    );
  }

  if (type === "stats") {
    return (
      <div className="grid gap-4 p-4 md:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-lg border p-4">
            <SkeletonBlock className="mb-3 h-5 w-32" />
            <div className="grid grid-cols-2 gap-2">
              <SkeletonBlock className="h-4 w-20" />
              <SkeletonBlock className="h-4 w-20" />
              <SkeletonBlock className="h-4 w-20" />
              <SkeletonBlock className="h-4 w-20" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  // graph
  return (
    <div className="flex flex-col items-center gap-4 p-4">
      {Array.from({ length: 3 }).map((_, i) => (
        <React.Fragment key={i}>
          <SkeletonBlock className="h-16 w-64 rounded-lg" />
          {i < 2 && <SkeletonBlock className="h-8 w-0.5" />}
        </React.Fragment>
      ))}
    </div>
  );
}

