/**
 * ColorLegend component tests.
 *
 * Validates gradient rendering for different colormaps, min/max label
 * display, mean indicator, collapse/expand toggle, and legend labels.
 * No network or browser-only APIs are needed — pure rendering tests.
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ColorLegend } from "../map-tools/ColorLegend";

// ---- Realistic config fixtures matching ResultTypeConfig ----

const NDVI_CONFIG = {
  label: "NDVI",
  unit: "index",
  formatValue: (v: number) => v.toFixed(2),
};

const NDVI_CONFIG_WITH_LABELS = {
  ...NDVI_CONFIG,
  legendLabels: { low: "Bare soil", high: "Dense vegetation" },
};

const RASTER_STATS = {
  min: -0.2,
  max: 0.9,
  mean: 0.45,
  std: 0.15,
  nodata_percent: 5.2,
};

describe("ColorLegend", () => {
  // ---- Basic rendering ----

  it("renders the result type label", () => {
    render(
      <ColorLegend
        vmin={-1}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );
    expect(screen.getByText("NDVI")).toBeInTheDocument();
  });

  it("renders the unit in parentheses", () => {
    render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );
    expect(screen.getByText("(index)")).toBeInTheDocument();
  });

  it("renders formatted min and max values", () => {
    render(
      <ColorLegend
        vmin={-0.5}
        vmax={0.8}
        colormap="ndvi"
        resultConfig={NDVI_CONFIG}
      />,
    );
    expect(screen.getByText("-0.50")).toBeInTheDocument();
    expect(screen.getByText("0.80")).toBeInTheDocument();
  });

  it("renders tick values at 25%, 50%, and 75% of the range", () => {
    render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );
    expect(screen.getByText("0.25")).toBeInTheDocument();
    expect(screen.getByText("0.50")).toBeInTheDocument();
    expect(screen.getByText("0.75")).toBeInTheDocument();
  });

  // ---- Gradient rendering ----

  it("renders a gradient bar element with CSS background", () => {
    const { container } = render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );
    const gradientBar = container.querySelector(".h-3.w-44");
    expect(gradientBar).toBeInTheDocument();
    expect(gradientBar?.getAttribute("style")).toContain("linear-gradient");
  });

  it("uses the correct gradient for the plasma colormap", () => {
    const { container } = render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="plasma"
        resultConfig={NDVI_CONFIG}
      />,
    );
    const gradientBar = container.querySelector(".h-3.w-44");
    // Plasma gradient starts with #0d0887
    expect(gradientBar?.getAttribute("style")).toContain("#0d0887");
  });

  it("falls back to viridis gradient for an unrecognized colormap", () => {
    const { container } = render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="nonexistent_colormap"
        resultConfig={NDVI_CONFIG}
      />,
    );
    const gradientBar = container.querySelector(".h-3.w-44");
    // Viridis starts with #440154
    expect(gradientBar?.getAttribute("style")).toContain("#440154");
  });

  // ---- Legend labels ----

  it("renders low and high legend labels when provided", () => {
    render(
      <ColorLegend
        vmin={-1}
        vmax={1}
        colormap="ndvi"
        resultConfig={NDVI_CONFIG_WITH_LABELS}
      />,
    );
    expect(screen.getByText("Bare soil")).toBeInTheDocument();
    expect(screen.getByText("Dense vegetation")).toBeInTheDocument();
  });

  it("omits legend labels when not provided in config", () => {
    render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );
    expect(screen.queryByText("Bare soil")).not.toBeInTheDocument();
  });

  // ---- Stats / Mean indicator ----

  it("displays the mean label when stats are provided", () => {
    render(
      <ColorLegend
        vmin={-0.2}
        vmax={0.9}
        colormap="ndvi"
        resultConfig={NDVI_CONFIG}
        stats={RASTER_STATS}
      />,
    );
    expect(screen.getByText("Mean: 0.45")).toBeInTheDocument();
  });

  it("omits the mean label when stats are not provided", () => {
    render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );
    expect(screen.queryByText(/Mean:/)).not.toBeInTheDocument();
  });

  // ---- Collapse / Expand ----

  it("collapses to a compact view when the collapse button is clicked", () => {
    render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );

    // Expanded: min/max labels visible
    expect(screen.getByText("0.00")).toBeInTheDocument();
    expect(screen.getByText("1.00")).toBeInTheDocument();

    // Click the collapse button
    const collapseButton = screen.getAllByRole("button")[0]!;
    fireEvent.click(collapseButton);

    // Collapsed: min/max labels hidden, label still shown
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
    expect(screen.queryByText("1.00")).not.toBeInTheDocument();
    expect(screen.getByText("NDVI")).toBeInTheDocument();
  });

  it("expands back to full view when the expand button is clicked", () => {
    render(
      <ColorLegend
        vmin={0}
        vmax={1}
        colormap="viridis"
        resultConfig={NDVI_CONFIG}
      />,
    );

    // Collapse first
    fireEvent.click(screen.getAllByRole("button")[0]!);
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();

    // Expand
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("0.00")).toBeInTheDocument();
    expect(screen.getByText("1.00")).toBeInTheDocument();
  });
});
