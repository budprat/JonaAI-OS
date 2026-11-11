import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AssetBriefing from "../AssetBriefing";
import {
  createSpy,
  installFetchSpy,
  jsonOk,
  resetSpies,
  resolveOnce,
} from "@/lib/spec-helpers";

const SAMPLE_DETAIL = {
  save_id: "save1234",
  title: "NDVI March Composite",
  result_path: "/tmp/openeo_results/results/job-1/result.tif",
  result_ext: "tif",
  result_filename: "result.tif",
  colormap: "viridis",
  size_bytes: 4096,
  created_at: "2024-03-04T12:00:00Z",
  source_query: "Generate NDVI composite for March 2024 over the northern basin.",
  job_id: "job-1",
  bounds: [[10.1, 20.2, 10.9, 20.8]],
  download_url: "/saved-jobs/save1234/files/result.tif",
  validation: {
    is_valid: true,
    warnings: ["Data is 91.2% NaN"],
  },
  statistics: {
    min: 0.11,
    max: 0.82,
    mean: 0.46,
    nan_percent: 91.2,
  },
  metadata: {
    format: "GTiff",
    validation: { is_valid: true, warnings: ["Data is 91.2% NaN"] },
    statistics: { min: 0.11, max: 0.82, mean: 0.46, nan_percent: 91.2 },
  },
  project: {
    id: "proj-1",
    name: "Northern Basin Monitoring",
    description: "Seasonal vegetation monitoring",
    analysis_count: 3,
    created_at: "2024-01-02T08:00:00Z",
    updated_at: "2024-03-03T09:30:00Z",
  },
  job_files: [
    {
      name: "result.tif",
      path: "/tmp/openeo_results/results/job-1/result.tif",
      ext: "tif",
      size_bytes: 4096,
      modified: "2024-03-04T12:00:00Z",
      download_url: "/saved-jobs/save1234/files/result.tif",
    },
  ],
};

describe("AssetBriefing", () => {
  let fetchSpy: ReturnType<typeof createSpy>;

  beforeEach(() => {
    resetSpies();
    fetchSpy = installFetchSpy();
    window.localStorage.setItem("jonaai-token", JSON.stringify("geo-token"));
  });

  it("loads a saved analysis detail route from the archive endpoint", async () => {
    resolveOnce(fetchSpy, jsonOk(SAMPLE_DETAIL));

    render(
      <MemoryRouter initialEntries={["/dashboard/asset/save1234"]}>
        <Routes>
          <Route path="/dashboard/asset/:assetId" element={<AssetBriefing />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("NDVI March Composite")).toBeInTheDocument();
    });

    expect(screen.getAllByText("Northern Basin Monitoring").length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText("Generate NDVI composite for March 2024 over the northern basin."),
    ).toBeInTheDocument();
    expect(screen.getByText("Data is 91.2% NaN")).toBeInTheDocument();
    expect(screen.getByText("Open Evidence Explorer (Preview)")).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledWith("/saved-jobs/save1234", {
      headers: { Authorization: "Bearer geo-token" },
    });
  });

  it("shows an error state when the saved analysis is missing", async () => {
    resolveOnce(fetchSpy, { ok: false, status: 404, json: async () => ({}) });

    render(
      <MemoryRouter initialEntries={["/dashboard/asset/save1234"]}>
        <Routes>
          <Route path="/dashboard/asset/:assetId" element={<AssetBriefing />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Detail page unavailable")).toBeInTheDocument();
    });

    expect(screen.getByText("Saved analysis not found.")).toBeInTheDocument();
  });
});
