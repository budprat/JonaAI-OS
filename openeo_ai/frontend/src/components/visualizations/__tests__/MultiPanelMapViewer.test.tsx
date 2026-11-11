/**
 * MultiPanelMapViewer auth tests.
 *
 * Panel raster overlays load via image-style requests that cannot carry an
 * Authorization header, so overlay URLs must embed the auth token via
 * authUrl(). Same defect class as the ComparisonMapViewer 401s seen live
 * on 2026-07-26. Heavy Leaflet dependencies are replaced with stand-ins
 * via the setup file.
 *
 * @vitest-environment jsdom
 */
import "../map-viewer-spec-setup";

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MultiPanelMapViewer } from "../MultiPanelMapViewer";
import type { MultiPanelVisualization, MapVisualization } from "@/types";

const TOKEN = "tok-test-456";

function makeRasterViz(url: string): MapVisualization {
  return {
    type: "raster",
    url,
    bounds: [
      [34.03, -118.55],
      [34.07, -118.51],
    ],
    colormap: "viridis",
  };
}

describe("MultiPanelMapViewer auth", () => {
  beforeEach(() => {
    window.localStorage.setItem("jonaai-token", JSON.stringify(TOKEN));
  });

  it("appends the auth token to every panel overlay URL", async () => {
    const data: MultiPanelVisualization = {
      panels: [
        {
          label: "Pre-fire",
          visualization: makeRasterViz("/render-raster?path=/tmp/pre.tif"),
        },
        {
          label: "Post-fire",
          visualization: makeRasterViz("/render-raster?path=/tmp/post.tif"),
        },
      ],
    };
    render(<MultiPanelMapViewer data={data} />);

    const overlays = await screen.findAllByTestId("image-overlay");
    expect(overlays).toHaveLength(2);
    for (const overlay of overlays) {
      expect(overlay.getAttribute("data-url")).toContain(
        `token=${encodeURIComponent(TOKEN)}`,
      );
    }
  });
});
