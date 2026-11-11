/**
 * VisualizationPanel component tests.
 *
 * Validates tab rendering, empty states, visualization type routing,
 * and auto-tab-switching behaviour. Heavy sub-components (MapViewer,
 * ChartViewer, etc.) are replaced with stand-in components via the
 * setup file to avoid jsdom limitations with Leaflet/canvas.
 *
 * @vitest-environment jsdom
 */
import "../viz-panel-spec-setup";

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { VisualizationPanel } from "../VisualizationPanel";
import type {
  Visualization,
  MapVisualization,
  ChartVisualization,
  ProcessGraphVisualization,
  ReportSection,
} from "@/types";

// ---- Helper to click a tab by its Radix trigger value ----
// Radix TabsTrigger renders as role="tab" with an id containing the value.
// Direct text matching fails because the trigger contains multiple spans.
// IMPORTANT: Radix uses onMouseDown (not onClick) to trigger onValueChange.
// The handler checks `event.button === 0 && event.ctrlKey === false`.
function clickTab(value: string) {
  const tabs = screen.getAllByRole("tab");
  const target = tabs.find((tab) => tab.id?.includes(value));
  if (!target) {
    throw new Error(`Tab trigger with value "${value}" not found`);
  }
  fireEvent.mouseDown(target, { button: 0 });
}

// ---- Realistic data factories matching project types ----

function makeMapViz(overrides?: Partial<Visualization>): Visualization {
  return {
    id: "map-1",
    type: "map",
    title: "NDVI Map",
    data: {
      type: "raster",
      url: "/render/ndvi-2024.tif",
      bounds: [
        [10, 75],
        [12, 77],
      ],
      colormap: "viridis",
      stats: {
        min: -0.1,
        max: 0.9,
        mean: 0.5,
        std: 0.2,
        nodata_percent: 3.5,
      },
    } as MapVisualization,
    ...overrides,
  };
}

function makeChartViz(overrides?: Partial<Visualization>): Visualization {
  return {
    id: "chart-1",
    type: "chart",
    title: "NDVI Time Series",
    data: {
      chartType: "line",
      title: "NDVI Time Series",
      xAxis: { label: "Date", values: ["2024-01", "2024-02", "2024-03"] },
      yAxis: { label: "NDVI" },
      series: [{ name: "Mean NDVI", values: [0.3, 0.5, 0.7] }],
    } as ChartVisualization,
    ...overrides,
  };
}

function makeProcessGraphViz(overrides?: Partial<Visualization>): Visualization {
  return {
    id: "pg-1",
    type: "process_graph",
    title: "Process Graph",
    data: {
      nodes: [
        {
          id: "load",
          process_id: "load_collection",
          arguments: { id: "SENTINEL2" },
          result: false,
        },
        {
          id: "save",
          process_id: "save_result",
          arguments: { format: "GTiff" },
          result: true,
        },
      ],
      edges: [{ source: "load", target: "save", argument: "data" }],
    } as ProcessGraphVisualization,
    ...overrides,
  };
}

function makeTableViz(overrides?: Partial<Visualization>): Visualization {
  return {
    id: "table-1",
    type: "table",
    title: "Band Statistics",
    data: {
      headers: ["Band", "Min", "Max"],
      rows: [
        ["B04", 0.01, 0.45],
        ["B08", 0.05, 0.85],
      ],
    },
    ...overrides,
  };
}

function makeReportSection(overrides?: Partial<ReportSection>): ReportSection {
  return {
    id: "report-1",
    type: "report",
    title: "Analysis Summary",
    content: "## Findings\n\nVegetation response is improving.",
    timestamp: new Date("2024-03-01T12:00:00Z"),
    toolName: "export_report",
    ...overrides,
  };
}

// ---- Tests ----

describe("VisualizationPanel", () => {
  // ---- Basic rendering ----

  it("renders the Results heading", () => {
    render(<VisualizationPanel visualizations={[]} />);
    expect(screen.getByText("Results")).toBeInTheDocument();
  });

  it("renders all five tab triggers", () => {
    render(<VisualizationPanel visualizations={[]} />);

    expect(screen.getByText("Map")).toBeInTheDocument();
    expect(screen.getByText("Report")).toBeInTheDocument();
    expect(screen.getAllByText("Charts").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Table/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Graph/).length).toBeGreaterThanOrEqual(1);
  });

  it("renders header badges", () => {
    render(<VisualizationPanel visualizations={[]} />);
    expect(screen.getByText("Deep Analysis")).toBeInTheDocument();
    expect(screen.getByText("GeoTIFF + JSON")).toBeInTheDocument();
  });

  // ---- Empty / loading states ----

  it("shows loading skeletons in non-active tabs when no data is present", () => {
    render(<VisualizationPanel visualizations={[]} />);
    clickTab("report");
    expect(screen.getByText("No reports yet")).toBeInTheDocument();
  });

  it("shows empty state in Visualization tab when no charts exist", async () => {
    render(<VisualizationPanel visualizations={[makeMapViz()]} />);

    clickTab("visualization");

    await waitFor(() => {
      expect(screen.getByText("No charts yet — run analysis to see plots")).toBeInTheDocument();
    });
  });

  // ---- Map tab ----

  it("renders MapViewer stand-in for a map visualization", async () => {
    render(<VisualizationPanel visualizations={[makeMapViz()]} />);
    expect(await screen.findByTestId("map-viewer")).toBeInTheDocument();
  });

  it("renders ComparisonMapViewer for comparison type", async () => {
    const compViz: Visualization = {
      id: "comp-1",
      type: "comparison",
      title: "Before/After",
      data: {
        before: { type: "raster", url: "/a.tif" },
        after: { type: "raster", url: "/b.tif" },
      },
    };
    render(<VisualizationPanel visualizations={[compViz]} />);
    // Lazy-loaded components render asynchronously via Suspense
    expect(await screen.findByTestId("comparison-viewer")).toBeInTheDocument();
  });

  // ---- Chart / Visualization tab ----

  it("renders ChartViewer in Visualization tab", async () => {
    render(<VisualizationPanel visualizations={[makeMapViz(), makeChartViz()]} />);

    clickTab("visualization");

    await waitFor(() => {
      expect(screen.getByTestId("chart-viewer")).toBeInTheDocument();
    });
    expect(screen.getAllByText("NDVI Time Series").length).toBeGreaterThanOrEqual(1);
  });

  // ---- Table tab ----

  it("renders data table content in Table tab", async () => {
    render(<VisualizationPanel visualizations={[makeTableViz()]} />);

    clickTab("table");

    await waitFor(() => {
      expect(screen.getByText("Band")).toBeInTheDocument();
    });
    expect(screen.getByText("B04")).toBeInTheDocument();
    expect(screen.getByText("B08")).toBeInTheDocument();
  });

  it("shows empty state for Table tab when no tables exist", async () => {
    render(<VisualizationPanel visualizations={[makeMapViz()]} />);

    clickTab("table");

    await waitFor(() => {
      expect(screen.getByText("No table data available yet")).toBeInTheDocument();
    });
  });

  it("survives a mounted table updating from empty to populated data", async () => {
    // Regression: DataTable returned early on empty headers BEFORE its
    // useMemo hook ran, so a rerender with populated data changed the hook
    // count and React threw "Rendered more hooks than during the previous
    // render". Reachable via key={table.id ?? idx} positional reuse.
    const emptyTable: Visualization = {
      id: "table-hooks-regression",
      type: "table",
      title: "Zonal Stats",
      data: { headers: [], rows: [] },
    };
    const { rerender } = render(<VisualizationPanel visualizations={[emptyTable]} />);

    clickTab("table");

    await waitFor(() => {
      expect(screen.getByText("No table data")).toBeInTheDocument();
    });

    const populated: Visualization = {
      ...emptyTable,
      data: { headers: ["Band", "Mean"], rows: [["red", 0.42]] },
    };
    rerender(<VisualizationPanel visualizations={[populated]} />);

    await waitFor(() => {
      expect(screen.getByText("Band")).toBeInTheDocument();
    });
    expect(screen.getByText("red")).toBeInTheDocument();
  });

  // ---- Process graph tab ----

  it("shows node count badge when process graph data exists", () => {
    render(<VisualizationPanel visualizations={[makeProcessGraphViz()]} />);
    // 2 nodes in the process graph
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  // ---- Report tab ----

  it("renders report content in the Report tab", async () => {
    render(
      <VisualizationPanel visualizations={[makeMapViz()]} reportSections={[makeReportSection()]} />
    );

    clickTab("report");

    await waitFor(() => {
      expect(screen.getByText("Analysis Summary")).toBeInTheDocument();
    });
    expect(screen.getByText("Vegetation response is improving.")).toBeInTheDocument();
  });

  // ---- Tab switching ----

  it("defaults to the map tab", async () => {
    render(<VisualizationPanel visualizations={[makeMapViz()]} />);
    expect(await screen.findByTestId("map-viewer")).toBeInTheDocument();
  });

  it("allows manual tab switching", async () => {
    render(<VisualizationPanel visualizations={[makeMapViz(), makeChartViz()]} />);

    expect(await screen.findByTestId("map-viewer")).toBeInTheDocument();

    clickTab("visualization");

    await waitFor(() => {
      expect(screen.getByTestId("chart-viewer")).toBeInTheDocument();
    });
  });

  // ---- Props ----

  it("passes onBboxChange to MapViewer without crashing", async () => {
    const onBboxChange = vi.fn();
    render(<VisualizationPanel visualizations={[makeMapViz()]} onBboxChange={onBboxChange} />);
    expect(await screen.findByTestId("map-viewer")).toBeInTheDocument();
  });

  it("applies className to the root element", () => {
    const { container } = render(
      <VisualizationPanel visualizations={[]} className="custom-panel-class" />
    );
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain("custom-panel-class");
  });
});
