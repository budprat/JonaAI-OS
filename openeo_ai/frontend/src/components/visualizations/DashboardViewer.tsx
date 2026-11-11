import { useMemo } from "react";
import type {
  DashboardVisualization,
  MapVisualization,
  ChartVisualization,
  StatCardData,
  TableVisualization,
} from "@/types";
import { MapViewer } from "./MapViewer";
import { ChartViewer } from "./ChartViewer";
import { cn } from "@/lib/utils";

type DashboardPanel = DashboardVisualization["panels"][0];
type MetricItem = StatCardData["metrics"][0];

interface DashboardViewerProps {
  data: DashboardVisualization;
  className?: string;
  title?: string;
}

function StatCard({ data, title }: { data: StatCardData; title?: string }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      {title && (
        <h4 className="mb-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {title}
        </h4>
      )}
      <div className="grid grid-cols-2 gap-2">
        {data.metrics.map((metric: MetricItem, i: number) => (
          <div key={i} className="flex flex-col">
            <span className="text-xs text-muted-foreground">
              {metric.label}
            </span>
            <span className="text-sm font-semibold tabular-nums">
              {typeof metric.value === "number"
                ? metric.value.toFixed(4)
                : metric.value}
              {metric.unit ? (
                <span className="ml-0.5 text-xs font-normal text-muted-foreground">
                  {metric.unit}
                </span>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MiniTable({
  data,
  title,
}: {
  data: TableVisualization;
  title?: string;
}) {
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border bg-card">
      {title && (
        <div className="border-b px-3 py-2">
          <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            {title}
          </h4>
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-muted">
            <tr>
              {data.headers.map((h: string, i: number) => (
                <th key={i} className="px-2 py-1 text-left font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row: (string | number)[], i: number) => (
              <tr key={i} className="border-t">
                {row.map((cell: string | number, j: number) => (
                  <td key={j} className="px-2 py-1 tabular-nums">
                    {typeof cell === "number" ? cell.toFixed(4) : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DashboardViewer({
  data,
  className,
  title,
}: DashboardViewerProps) {
  const { panels } = data;

  // Separate panel types for layout
  const mapPanels = useMemo(
    () => panels.filter((p: DashboardPanel) => p.type === "map"),
    [panels],
  );
  const chartPanels = useMemo(
    () => panels.filter((p: DashboardPanel) => p.type === "chart"),
    [panels],
  );
  const statPanels = useMemo(
    () => panels.filter((p: DashboardPanel) => p.type === "stat"),
    [panels],
  );
  const tablePanels = useMemo(
    () => panels.filter((p: DashboardPanel) => p.type === "table"),
    [panels],
  );

  if (panels.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        No dashboard panels to display
      </div>
    );
  }

  // If any panel has gridArea, use CSS grid with named areas
  const hasGridAreas = panels.some((p: DashboardPanel) => p.gridArea);

  if (hasGridAreas) {
    // Build grid-template-areas from panel gridArea strings
    const areas = new Set(
      panels.map((p: DashboardPanel) => p.gridArea).filter(Boolean),
    );
    return (
      <div className={cn("flex h-full flex-col gap-2", className)}>
        {title && <h3 className="px-1 text-sm font-medium">{title}</h3>}
        <div
          className="grid flex-1 gap-2"
          style={{
            gridTemplateAreas: Array.from(areas).join(" "),
            gridTemplateColumns: "1fr 1fr 1fr",
            gridTemplateRows: "1fr 1fr",
          }}
        >
          {panels.map((panel: DashboardPanel, idx: number) => (
            <div
              key={idx}
              className="min-h-0 overflow-hidden"
              style={{ gridArea: panel.gridArea }}
            >
              <PanelRenderer panel={panel} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Default layout: map on top, stats on right, chart on bottom
  return (
    <div className={cn("flex h-full flex-col gap-2", className)}>
      {title && <h3 className="px-1 text-sm font-medium">{title}</h3>}

      {/* Top row: Map + Stats */}
      <div className="flex flex-1 gap-2 min-h-0">
        {/* Map panel (takes most space) */}
        {mapPanels.length > 0 && (
          <div className="flex-[2] min-w-0 overflow-hidden rounded-lg border">
            <MapViewer
              data={mapPanels[0]!.data as MapVisualization}
              title={mapPanels[0]!.title}
              className="h-full w-full"
            />
          </div>
        )}

        {/* Stats sidebar */}
        {statPanels.length > 0 && (
          <div className="flex flex-1 flex-col gap-2 overflow-auto">
            {statPanels.map((panel: DashboardPanel, idx: number) => (
              <StatCard
                key={idx}
                data={panel.data as StatCardData}
                title={panel.title}
              />
            ))}
          </div>
        )}
      </div>

      {/* Bottom row: Charts and Tables */}
      {(chartPanels.length > 0 || tablePanels.length > 0) && (
        <div className="flex gap-2 min-h-[200px]">
          {chartPanels.map((panel: DashboardPanel, idx: number) => (
            <div
              key={`chart-${idx}`}
              className="flex-1 overflow-hidden rounded-lg border"
            >
              <ChartViewer
                data={panel.data as ChartVisualization}
                className="h-full"
              />
            </div>
          ))}
          {tablePanels.map((panel: DashboardPanel, idx: number) => (
            <div key={`table-${idx}`} className="flex-1 overflow-hidden">
              <MiniTable
                data={panel.data as TableVisualization}
                title={panel.title}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Generic panel renderer for grid-area layouts
function PanelRenderer({ panel }: { panel: DashboardPanel }) {
  switch (panel.type) {
    case "map":
      return (
        <MapViewer
          data={panel.data as MapVisualization}
          title={panel.title}
          className="h-full w-full"
        />
      );
    case "chart":
      return (
        <ChartViewer
          data={panel.data as ChartVisualization}
          className="h-full"
        />
      );
    case "stat":
      return <StatCard data={panel.data as StatCardData} title={panel.title} />;
    case "table":
      return (
        <MiniTable
          data={panel.data as TableVisualization}
          title={panel.title}
        />
      );
    default:
      return (
        <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
          Unknown panel type
        </div>
      );
  }
}
