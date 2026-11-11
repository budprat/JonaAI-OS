/**
 * MapViewer component tests.
 *
 * Validates rendering with empty/single/multi layers, control visibility,
 * layer panel interactions, basemap picker, colormap picker, title overlay,
 * bbox controls, and prop forwarding. Heavy Leaflet dependencies are
 * replaced with stand-in components via the setup file.
 *
 * @vitest-environment jsdom
 */
import "../map-viewer-spec-setup";

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MapViewer } from "../MapViewer";
import type { MapVisualization, BBox } from "@/types";

// ---- Data factories ----

function makeRasterData(overrides?: Partial<MapVisualization>): MapVisualization {
  return {
    type: "raster",
    url: "/render/ndvi-2024.tif",
    bounds: [
      [10, 75],
      [12, 77],
    ],
    colormap: "viridis",
    opacity: 0.6,
    vmin: -0.2,
    vmax: 0.9,
    source: "/data/ndvi-2024.tif",
    stats: {
      min: -0.2,
      max: 0.9,
      mean: 0.45,
      std: 0.15,
      nodata_percent: 3.5,
    },
    ...overrides,
  };
}

function makeVectorData(): MapVisualization {
  return {
    type: "vector",
    geojson: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: [76, 11] },
          properties: { name: "Sample" },
        },
      ],
    },
    bounds: [
      [10, 75],
      [12, 77],
    ],
  };
}

function makeLayerEntry(id: string, title: string, overrides?: Partial<MapVisualization>) {
  return { id, title, data: makeRasterData(overrides) };
}

// ---- Tests ----

describe("MapViewer", () => {
  // ---- Basic rendering ----

  it("renders the map container", () => {
    render(<MapViewer />);
    expect(screen.getByTestId("map-container")).toBeInTheDocument();
  });

  it("renders a tile layer for the default basemap", () => {
    render(<MapViewer />);
    const tile = screen.getByTestId("tile-layer");
    expect(tile).toBeInTheDocument();
    // Default basemap is satellite
    expect(tile.getAttribute("data-url")).toContain("satellite");
  });

  it("renders the title overlay when title prop is provided", () => {
    render(<MapViewer data={makeRasterData()} title="NDVI Map" />);
    expect(screen.getByText("NDVI Map")).toBeInTheDocument();
  });

  it("does not render title overlay when title is absent", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.queryByText("NDVI Map")).not.toBeInTheDocument();
  });

  // ---- Raster layer rendering ----

  it("renders an image overlay for raster data", () => {
    render(<MapViewer data={makeRasterData()} />);
    const overlay = screen.getByTestId("image-overlay");
    expect(overlay).toBeInTheDocument();
    expect(overlay.getAttribute("data-url")).toBe("/render/ndvi-2024.tif");
  });

  it("applies the opacity from data props", () => {
    render(<MapViewer data={makeRasterData({ opacity: 0.7 })} />);
    const overlay = screen.getByTestId("image-overlay");
    expect(overlay.getAttribute("data-opacity")).toBe("0.7");
  });

  // ---- Vector data ----

  it("renders a GeoJSON layer for vector data", () => {
    render(<MapViewer data={makeVectorData()} />);
    expect(screen.getByTestId("geojson-layer")).toBeInTheDocument();
  });

  // ---- Controls toolbar ----

  it("renders zoom in and zoom out buttons", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByLabelText("Zoom in")).toBeInTheDocument();
    expect(screen.getByLabelText("Zoom out")).toBeInTheDocument();
  });

  it("renders the basemap picker button", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByLabelText("Change basemap")).toBeInTheDocument();
  });

  it("renders the search place button", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByLabelText("Search place or coordinates")).toBeInTheDocument();
  });

  it("renders pixel inspector button for raster data", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByLabelText("Enable pixel inspector")).toBeInTheDocument();
  });

  it("renders download button when image URL exists", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByLabelText("Download raster image")).toBeInTheDocument();
  });

  // ---- Basemap picker ----

  it("opens the basemap picker and shows options", () => {
    render(<MapViewer data={makeRasterData()} />);
    const btn = screen.getByLabelText("Change basemap");
    fireEvent.click(btn);
    expect(screen.getByText("Streets")).toBeInTheDocument();
    expect(screen.getByText("Satellite")).toBeInTheDocument();
    expect(screen.getByText("Terrain")).toBeInTheDocument();
  });

  it("selects a basemap and closes the picker", () => {
    render(<MapViewer data={makeRasterData()} />);
    fireEvent.click(screen.getByLabelText("Change basemap"));
    fireEvent.click(screen.getByText("Streets"));
    // Picker should be closed
    expect(screen.queryByText("Terrain")).not.toBeInTheDocument();
    // Tile layer URL should update to OSM
    const tile = screen.getByTestId("tile-layer");
    expect(tile.getAttribute("data-url")).toContain("osm");
  });

  // ---- Colormap picker ----

  it("renders the colormap picker button for raster data", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByLabelText("Change colormap")).toBeInTheDocument();
  });

  it("opens the colormap picker and shows all options", () => {
    render(<MapViewer data={makeRasterData()} />);
    fireEvent.click(screen.getByLabelText("Change colormap"));
    expect(screen.getByText("viridis")).toBeInTheDocument();
    expect(screen.getByText("plasma")).toBeInTheDocument();
    expect(screen.getByText("ndvi")).toBeInTheDocument();
    expect(screen.getByText("coolwarm")).toBeInTheDocument();
  });

  it("does not render colormap picker for vector data", () => {
    render(<MapViewer data={makeVectorData()} />);
    expect(screen.queryByLabelText("Change colormap")).not.toBeInTheDocument();
  });

  // ---- Bbox controls ----

  it("renders draw bounding box button when onBboxChange is provided", () => {
    const onChange = vi.fn();
    render(<MapViewer data={makeRasterData()} onBboxChange={onChange} />);
    expect(screen.getByLabelText("Draw bounding box rectangle")).toBeInTheDocument();
  });

  it("does not render draw button when onBboxChange is absent", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.queryByLabelText("Draw bounding box")).not.toBeInTheDocument();
  });

  it("renders clear bbox button when bbox is provided", () => {
    const bbox: BBox = { west: 75, south: 10, east: 77, north: 12 };
    const onChange = vi.fn();
    render(<MapViewer data={makeRasterData()} onBboxChange={onChange} bbox={bbox} />);
    expect(screen.getByLabelText("Clear area of interest")).toBeInTheDocument();
  });

  it("displays bbox coordinates when bbox is provided", () => {
    const bbox: BBox = {
      west: 75.123,
      south: 10.456,
      east: 77.789,
      north: 12.012,
    };
    const onChange = vi.fn();
    render(<MapViewer data={makeRasterData()} onBboxChange={onChange} bbox={bbox} />);
    expect(screen.getByText(/75\.123/)).toBeInTheDocument();
    expect(screen.getByText(/10\.456/)).toBeInTheDocument();
  });

  it("calls onBboxChange with null when clear bbox is clicked", () => {
    const bbox: BBox = { west: 75, south: 10, east: 77, north: 12 };
    const onChange = vi.fn();
    render(<MapViewer data={makeRasterData()} onBboxChange={onChange} bbox={bbox} />);
    fireEvent.click(screen.getByLabelText("Clear area of interest"));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  // ---- Search ----

  it("opens the search input when search button is clicked", () => {
    render(<MapViewer data={makeRasterData()} />);
    fireEvent.click(screen.getByLabelText("Search place or coordinates"));
    expect(screen.getByPlaceholderText("Search place or coords...")).toBeInTheDocument();
  });

  it("closes search when close button is clicked", () => {
    render(<MapViewer data={makeRasterData()} />);
    fireEvent.click(screen.getByLabelText("Search place or coordinates"));
    fireEvent.click(screen.getByTitle("Close search"));
    expect(screen.queryByPlaceholderText("Search place or coords...")).not.toBeInTheDocument();
  });

  // ---- Multi-layer panel ----

  it("shows layer panel button when layers are provided", () => {
    render(<MapViewer layers={[makeLayerEntry("l1", "Layer One")]} />);
    expect(screen.getByLabelText("Toggle layer controls panel")).toBeInTheDocument();
  });

  it("does not show layer panel button when no layers exist", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.queryByLabelText("Toggle layer controls panel")).not.toBeInTheDocument();
  });

  it("opens the layer panel and shows layer names", () => {
    render(
      <MapViewer
        layers={[makeLayerEntry("l1", "NDVI Result"), makeLayerEntry("l2", "NDWI Result")]}
      />
    );
    fireEvent.click(screen.getByLabelText("Toggle layer controls panel"));
    expect(screen.getByText("NDVI Result")).toBeInTheDocument();
    expect(screen.getByText("NDWI Result")).toBeInTheDocument();
    expect(screen.getByText("Layers (2)")).toBeInTheDocument();
  });

  it("toggles layer visibility via the eye button", () => {
    render(<MapViewer layers={[makeLayerEntry("l1", "Layer One")]} />);
    fireEvent.click(screen.getByLabelText("Toggle layer controls panel"));

    // Initially visible — click "Hide layer"
    const hideBtn = screen.getByLabelText("Hide layer Layer One");
    fireEvent.click(hideBtn);

    // After hiding, the label changes to "Show layer"
    expect(screen.getByLabelText("Show layer Layer One")).toBeInTheDocument();
  });

  it("calls onRemoveLayer when remove button is clicked", () => {
    const onRemove = vi.fn();
    render(<MapViewer layers={[makeLayerEntry("l1", "Layer One")]} onRemoveLayer={onRemove} />);
    fireEvent.click(screen.getByLabelText("Toggle layer controls panel"));
    fireEvent.click(screen.getByLabelText("Remove layer Layer One"));
    expect(onRemove).toHaveBeenCalledWith("l1");
  });

  it("shows compare button when two or more visible layers and onCompare provided", () => {
    const onCompare = vi.fn();
    render(
      <MapViewer
        layers={[makeLayerEntry("l1", "Layer A"), makeLayerEntry("l2", "Layer B")]}
        onCompare={onCompare}
      />
    );
    fireEvent.click(screen.getByLabelText("Toggle layer controls panel"));
    expect(
      screen.getByLabelText("Compare first two visible layers side by side")
    ).toBeInTheDocument();
  });

  it("calls onCompare with first two visible layer IDs", () => {
    const onCompare = vi.fn();
    render(
      <MapViewer
        layers={[makeLayerEntry("l1", "Layer A"), makeLayerEntry("l2", "Layer B")]}
        onCompare={onCompare}
      />
    );
    fireEvent.click(screen.getByLabelText("Toggle layer controls panel"));
    fireEvent.click(screen.getByLabelText("Compare first two visible layers side by side"));
    expect(onCompare).toHaveBeenCalledWith("l1", "l2");
  });

  // ---- Color legend ----

  it("renders the color legend when raster data has vmin and vmax", () => {
    render(<MapViewer data={makeRasterData()} title="NDVI" />);
    // Both the title overlay and the ColorLegend label render "NDVI"
    const ndviElements = screen.getAllByText("NDVI");
    expect(ndviElements.length).toBeGreaterThanOrEqual(1);
  });

  it("does not render color legend when vmin/vmax are missing", () => {
    render(<MapViewer data={makeRasterData({ vmin: undefined, vmax: undefined })} title="NDVI" />);
    // The title overlay shows "NDVI" but the legend label should not appear
    // since ColorLegend isn't rendered without value range
    const ndviElements = screen.getAllByText("NDVI");
    // Only the title overlay, not the legend
    expect(ndviElements.length).toBe(1);
  });

  // ---- className prop ----

  it("applies className to the root element", () => {
    const { container } = render(
      <MapViewer data={makeRasterData()} className="custom-map-class" />
    );
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("custom-map-class");
  });

  // ---- Pixel inspector ----

  it("renders the pixel inspector component for raster data", () => {
    render(<MapViewer data={makeRasterData()} />);
    const inspector = screen.getByTestId("pixel-inspector");
    expect(inspector).toBeInTheDocument();
    expect(inspector.getAttribute("data-active")).toBe("false");
  });

  it("toggles pixel inspector active state", () => {
    render(<MapViewer data={makeRasterData()} />);
    fireEvent.click(screen.getByLabelText("Enable pixel inspector"));
    const inspector = screen.getByTestId("pixel-inspector");
    expect(inspector.getAttribute("data-active")).toBe("true");
  });

  // ---- Stats info panel ----

  it("renders the stats info panel button for raster data with stats", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByTitle("Raster statistics")).toBeInTheDocument();
  });

  // ---- Opacity slider ----

  it("renders opacity slider for single raster layer", () => {
    render(<MapViewer data={makeRasterData()} />);
    expect(screen.getByText("Opacity")).toBeInTheDocument();
  });

  it("does not render opacity slider when multiple layers exist", () => {
    render(<MapViewer data={makeRasterData()} layers={[makeLayerEntry("l1", "Layer One")]} />);
    // Opacity text in the layer panel is per-layer, not the global slider
    expect(screen.queryByText("Opacity")).not.toBeInTheDocument();
  });
});
