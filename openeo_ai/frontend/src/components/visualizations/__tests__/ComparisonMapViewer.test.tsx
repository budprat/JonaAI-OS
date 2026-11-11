/**
 * ComparisonMapViewer auth tests.
 *
 * Raster overlays load via image-style requests that cannot carry an
 * Authorization header, so overlay URLs must embed the auth token via
 * authUrl(). Regression test for the live 2026-07-26 Palisades run where
 * both comparison panes fetched /render-raster without a token and got 401s.
 * Heavy Leaflet dependencies are replaced with stand-ins via the setup file.
 *
 * @vitest-environment jsdom
 */
import "../map-viewer-spec-setup";

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ComparisonMapViewer } from "../ComparisonMapViewer";
import type { ComparisonVisualization, MapVisualization } from "@/types";

const TOKEN = "tok-test-123";

function makeRasterViz(url: string): MapVisualization {
  return {
    type: "raster",
    url,
    bounds: [
      [34.03, -118.55],
      [34.07, -118.51],
    ],
    colormap: "RdYlGn_r",
  };
}

describe("ComparisonMapViewer auth", () => {
  beforeEach(() => {
    window.localStorage.setItem("jonaai-token", JSON.stringify(TOKEN));
  });

  it("appends the auth token to both pane overlay URLs", async () => {
    const data: ComparisonVisualization = {
      before: makeRasterViz("/render-raster?path=/tmp/before.tif&colormap=viridis"),
      after: makeRasterViz("/render-raster?path=/tmp/after.tif&colormap=viridis"),
    };
    render(<ComparisonMapViewer data={data} />);

    const overlays = await screen.findAllByTestId("image-overlay");
    expect(overlays).toHaveLength(2);
    for (const overlay of overlays) {
      expect(overlay.getAttribute("data-url")).toContain(
        `token=${encodeURIComponent(TOKEN)}`,
      );
    }
  });
});
