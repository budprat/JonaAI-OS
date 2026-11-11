/**
 * TimelapseViewer auth tests.
 *
 * The current-frame raster overlay loads via an image-style request that
 * cannot carry an Authorization header, so its URL must embed the auth
 * token via authUrl(). Same defect class as the ComparisonMapViewer 401s
 * seen live on 2026-07-26. Heavy Leaflet dependencies are replaced with
 * stand-ins via the setup file.
 *
 * @vitest-environment jsdom
 */
import "../map-viewer-spec-setup";

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { TimelapseViewer } from "../TimelapseViewer";
import type { TimelapseVisualization } from "@/types";

const TOKEN = "tok-test-789";

describe("TimelapseViewer auth", () => {
  beforeEach(() => {
    window.localStorage.setItem("jonaai-token", JSON.stringify(TOKEN));
  });

  it("appends the auth token to the current layer overlay URL", async () => {
    const data: TimelapseVisualization = {
      layers: [
        {
          timestamp: "2025-01-01",
          url: "/render-raster?path=/tmp/frame1.tif",
          bounds: [
            [34.03, -118.55],
            [34.07, -118.51],
          ],
        },
      ],
      colormap: "viridis",
    };
    render(<TimelapseViewer data={data} />);

    const overlay = await screen.findByTestId("image-overlay");
    expect(overlay.getAttribute("data-url")).toContain(
      `token=${encodeURIComponent(TOKEN)}`,
    );
  });
});
