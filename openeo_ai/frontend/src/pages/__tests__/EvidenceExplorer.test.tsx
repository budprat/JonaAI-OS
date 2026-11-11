import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import EvidenceExplorer from "../EvidenceExplorer";
import {
  createSpy,
  installFetchSpy,
  jsonOk,
  resetSpies,
  resolveOnce,
  resolveWith,
} from "@/lib/spec-helpers";

const SAMPLE_DETAIL = {
  save_id: "test1234",
  title: "Nazca NDVI Analysis",
  result_path: "/tmp/results/test1234/result.tif",
  result_ext: "tif",
  result_filename: "result.tif",
  colormap: "viridis",
  size_bytes: 8192,
  bounds: [-75.18, -14.78, -75.08, -14.68],
  vmin: 0.0,
  vmax: 1.0,
  source_query: "NDVI analysis of Nazca",
  statistics: { min: 0.1, max: 0.95, mean: 0.52 },
  validation: { is_valid: true, warnings: [] },
  download_url: "/saved-jobs/test1234/files/result.tif",
  job_files: [
    {
      name: "result.tif",
      path: "/tmp/result.tif",
      ext: "tif",
      size_bytes: 8192,
      modified: "2026-03-20",
    },
  ],
  created_at: "2026-03-20T10:00:00Z",
};

function renderExplorer(assetId = "test1234") {
  return render(
    <MemoryRouter initialEntries={[`/dashboard/asset/${assetId}/evidence`]}>
      <Routes>
        <Route path="/dashboard/asset/:assetId/evidence" element={<EvidenceExplorer />} />
        {/* Catch canonical navigation to saved analysis briefing */}
        <Route
          path="/dashboard/analysis/:analysisId"
          element={<div data-testid="briefing-page">Analysis Briefing</div>}
        />
        {/* Legacy briefing route remains supported */}
        <Route
          path="/dashboard/asset/:assetId"
          element={<div data-testid="briefing-page">Asset Briefing</div>}
        />
        <Route path="/dashboard" element={<div data-testid="dashboard-page">Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("EvidenceExplorer", () => {
  let fetchSpy: ReturnType<typeof createSpy>;

  beforeEach(() => {
    resetSpies();
    fetchSpy = installFetchSpy();
    window.localStorage.setItem("jonaai-token", JSON.stringify("geo-token"));
  });

  it("shows a loading spinner while fetching data", () => {
    // Configure fetch to return a promise that never settles,
    // keeping the component in loading state indefinitely.
    resolveWith(fetchSpy, new Promise(() => {}));

    renderExplorer();

    expect(screen.getByText("Loading evidence...")).toBeInTheDocument();
  });

  it("shows an error message when the fetch returns 404", async () => {
    resolveOnce(fetchSpy, { ok: false, status: 404, json: async () => ({}) });

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Saved analysis not found.")).toBeInTheDocument();
    });

    expect(screen.getByText("Back to Dashboard")).toBeInTheDocument();
  });

  it("shows a generic error message when the fetch fails with a non-404 status", async () => {
    resolveOnce(fetchSpy, { ok: false, status: 500, json: async () => ({}) });

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Failed to load evidence.")).toBeInTheDocument();
    });
  });

  it("renders the title, telemetry bar, and raster image on successful fetch", async () => {
    resolveOnce(fetchSpy, jsonOk(SAMPLE_DETAIL));

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Nazca NDVI Analysis")).toBeInTheDocument();
    });

    // Telemetry bar items
    expect(screen.getByText("VIRIDIS")).toBeInTheDocument();
    // "8 KB" appears in both telemetry bar and result files panel
    expect(screen.getAllByText("8 KB").length).toBeGreaterThanOrEqual(1);
    // source_query appears in both telemetry bar and the source query panel
    expect(screen.getAllByText("NDVI analysis of Nazca").length).toBeGreaterThanOrEqual(1);

    // Raster image rendered with correct src
    const img = screen.getByAltText("Nazca NDVI Analysis");
    expect(img).toBeInTheDocument();
    expect(img.getAttribute("src")).toContain("/render-raster?path=");
    expect(img.getAttribute("src")).toContain("colormap=viridis");
    expect(img.getAttribute("src")).toContain("vmin=0");
    expect(img.getAttribute("src")).toContain("vmax=1");

    // Statistics rendered
    expect(screen.getByText("min")).toBeInTheDocument();
    expect(screen.getByText("0.1000")).toBeInTheDocument();
    expect(screen.getByText("max")).toBeInTheDocument();
    expect(screen.getByText("0.9500")).toBeInTheDocument();
    expect(screen.getByText("mean")).toBeInTheDocument();
    expect(screen.getByText("0.5200")).toBeInTheDocument();

    // Result files section
    expect(screen.getByText("result.tif")).toBeInTheDocument();

    // Fetch called with correct auth
    expect(fetchSpy).toHaveBeenCalledWith("/saved-jobs/test1234", {
      headers: { Authorization: "Bearer geo-token" },
    });
  });

  it("switches colormap when a different radio button is selected", async () => {
    resolveOnce(fetchSpy, jsonOk(SAMPLE_DETAIL));

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Nazca NDVI Analysis")).toBeInTheDocument();
    });

    // Default colormap should be viridis (from data)
    const viridisRadio = screen.getByLabelText("Viridis") as HTMLInputElement;
    expect(viridisRadio.checked).toBe(true);

    // Switch to Blues
    const bluesRadio = screen.getByLabelText("Blues") as HTMLInputElement;
    fireEvent.click(bluesRadio);

    expect(bluesRadio.checked).toBe(true);
    expect(viridisRadio.checked).toBe(false);

    // Raster image src should update to new colormap
    const img = screen.getByAltText("Nazca NDVI Analysis");
    expect(img.getAttribute("src")).toContain("colormap=Blues");

    // Telemetry bar should update
    expect(screen.getByText("BLUES")).toBeInTheDocument();
  });

  it("navigates to the asset briefing page when the back button is clicked", async () => {
    resolveOnce(fetchSpy, jsonOk(SAMPLE_DETAIL));

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Nazca NDVI Analysis")).toBeInTheDocument();
    });

    const backButton = screen.getByLabelText("Back to briefing");
    fireEvent.click(backButton);

    await waitFor(() => {
      expect(screen.getByTestId("briefing-page")).toBeInTheDocument();
    });
  });

  it("navigates to dashboard when Back to Dashboard is clicked from error state", async () => {
    resolveOnce(fetchSpy, { ok: false, status: 404, json: async () => ({}) });

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Back to Dashboard")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Back to Dashboard"));

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
    });
  });

  it("renders bounds text from flat bounds array", async () => {
    resolveOnce(fetchSpy, jsonOk(SAMPLE_DETAIL));

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Nazca NDVI Analysis")).toBeInTheDocument();
    });

    // Bounds should be formatted as comma-separated fixed-4 decimals
    const boundsText = "-75.1800, -14.7800, -75.0800, -14.6800";
    // Bounds appear in both the telemetry bar and the spatial extent panel
    const boundsElements = screen.getAllByText(boundsText);
    expect(boundsElements.length).toBeGreaterThanOrEqual(1);
  });

  it("renders validation warnings when present", async () => {
    const detailWithWarnings = {
      ...SAMPLE_DETAIL,
      validation: { is_valid: true, warnings: ["Data is 85.3% NaN"] },
    };
    resolveOnce(fetchSpy, jsonOk(detailWithWarnings));

    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Nazca NDVI Analysis")).toBeInTheDocument();
    });

    expect(screen.getByText("Validation Warnings")).toBeInTheDocument();
    expect(screen.getByText("Data is 85.3% NaN")).toBeInTheDocument();
  });
});
