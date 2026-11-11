/**
 * Setup file for VisualizationPanel specs.
 *
 * Substitutes heavy child components that depend on browser APIs
 * unavailable in jsdom (Leaflet canvas, WebGL) with lightweight
 * stand-in components. This enables verifying the panel's tab logic,
 * routing, and state management without pulling in the full map stack.
 */
import { vi } from "vitest";

/* ---------- Stand-in component factories ---------- */

function makeStandIn(labelId: string) {
  return function StandInComponent(props: Record<string, unknown>) {
    return (
      <div data-testid={labelId}>
        {(props as { title?: string }).title ?? labelId}
      </div>
    );
  };
}

/* ---------- Module substitutions ---------- */
// These components import Leaflet/react-leaflet which require
// Canvas and DOM measurement APIs that jsdom does not provide.

vi.mock("./MapViewer", () => ({
  MapViewer: makeStandIn("map-viewer"),
}));

vi.mock("./ComparisonMapViewer", () => ({
  ComparisonMapViewer: makeStandIn("comparison-viewer"),
}));

vi.mock("./MultiPanelMapViewer", () => ({
  MultiPanelMapViewer: makeStandIn("multi-panel-viewer"),
}));

vi.mock("./TimelapseViewer", () => ({
  TimelapseViewer: makeStandIn("timelapse-viewer"),
}));

vi.mock("./DashboardViewer", () => ({
  DashboardViewer: makeStandIn("dashboard-viewer"),
}));

vi.mock("./ChartViewer", () => ({
  ChartViewer: function ChartStandIn(props: { data: { title: string } }) {
    return <div data-testid="chart-viewer">{props.data.title}</div>;
  },
}));

vi.mock("leaflet", () => ({
  default: {
    icon: vi.fn(),
    divIcon: vi.fn(),
    DomUtil: { create: vi.fn() },
  },
  icon: vi.fn(),
  divIcon: vi.fn(),
}));

vi.mock("react-leaflet", () => ({
  MapContainer: makeStandIn("map-container"),
  TileLayer: makeStandIn("tile-layer"),
  useMap: () => ({
    fitBounds: vi.fn(),
    getContainer: vi.fn(() => document.createElement("div")),
  }),
}));
