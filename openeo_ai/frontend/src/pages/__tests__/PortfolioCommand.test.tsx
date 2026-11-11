import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PortfolioCommand from "../PortfolioCommand";
import {
  createSpy,
  installFetchSpy,
  jsonOk,
  resetSpies,
  resolveOnce,
} from "@/lib/spec-helpers";

const SAMPLE_PROJECTS = [
  {
    id: "proj-1",
    name: "Northern Basin Monitoring",
    description: "Seasonal vegetation monitoring",
    analysis_count: 2,
    created_at: "2024-01-02T08:00:00Z",
    updated_at: "2024-03-03T09:30:00Z",
    files: [
      {
        save_id: "save-1",
        title: "NDVI March",
        result_path: "/tmp/ndvi-march.tif",
        size_bytes: 2048,
        created_at: "2024-03-03T09:30:00Z",
      },
    ],
  },
];

const SAMPLE_UNASSIGNED = [
  {
    save_id: "save-2",
    title: "Unassigned Change Detection",
    result_path: "/tmp/change.tif",
    size_bytes: 4096,
    created_at: "2024-03-04T12:00:00Z",
  },
];

const SAMPLE_JOBS = [
  {
    save_id: "save-1",
    title: "NDVI March",
    result_path: "/tmp/ndvi-march.tif",
    size_bytes: 2048,
    colormap: "viridis",
    created_at: "2024-03-03T09:30:00Z",
  },
  {
    save_id: "save-2",
    title: "Unassigned Change Detection",
    result_path: "/tmp/change.tif",
    size_bytes: 4096,
    colormap: "magma",
    created_at: "2024-03-04T12:00:00Z",
  },
];

describe("PortfolioCommand", () => {
  let fetchSpy: ReturnType<typeof createSpy>;

  beforeEach(() => {
    resetSpies();
    fetchSpy = installFetchSpy();
    window.localStorage.setItem("jonaai-token", JSON.stringify("geo-token"));
  });

  it("loads live portfolio data from projects and saved jobs", async () => {
    resolveOnce(
      fetchSpy,
      jsonOk({ projects: SAMPLE_PROJECTS, unassigned_files: SAMPLE_UNASSIGNED }),
    );
    resolveOnce(fetchSpy, jsonOk({ jobs: SAMPLE_JOBS }));

    render(
      <MemoryRouter>
        <PortfolioCommand />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Northern Basin Monitoring")).toBeInTheDocument();
    });

    expect(screen.getByText("Live Portfolio")).toBeInTheDocument();
    expect(screen.getAllByText("Unassigned Change Detection").length).toBeGreaterThanOrEqual(1);
    expect(fetchSpy).toHaveBeenCalledWith("/api/projects", {
      headers: { Authorization: "Bearer geo-token" },
    });
    expect(fetchSpy).toHaveBeenCalledWith("/saved-jobs?limit=100", {
      headers: { Authorization: "Bearer geo-token" },
    });
  });

  it("shows the empty state when no projects or saved analyses exist", async () => {
    resolveOnce(fetchSpy, jsonOk({ projects: [], unassigned_files: [] }));
    resolveOnce(fetchSpy, jsonOk({ jobs: [] }));

    render(
      <MemoryRouter>
        <PortfolioCommand />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("No projects or saved analyses yet.")).toBeInTheDocument();
    });
  });
});
