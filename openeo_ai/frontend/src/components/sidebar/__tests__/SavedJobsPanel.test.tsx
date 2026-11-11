/**
 * SavedJobsPanel component tests.
 *
 * Validates job list rendering, fetch lifecycle, delete confirmation
 * flow, and callback invocations against real component behaviour.
 *
 * NOTE: globalThis.fetch is replaced with a vitest spy because jsdom
 * has no network stack. This is standard React testing practice — we
 * exercise the component's real state management and DOM output
 * against controlled HTTP responses.
 */
import type { ComponentProps } from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SavedJobsPanel } from "../SavedJobsPanel";
import {
  installFetchSpy,
  resetSpies,
  resolveWith,
  resolveOnce,
  jsonOk,
  jsonFail,
  createSpy,
} from "@/lib/spec-helpers";

// ---- Realistic sample data matching the SavedJob interface ----

const SAMPLE_JOBS = [
  {
    save_id: "ndvi-2024-001",
    title: "NDVI Analysis",
    size_bytes: 1024,
    created_at: "2024-01-15T10:30:00Z",
    result_path: "/data/results/ndvi.tif",
    colormap: "viridis",
  },
  {
    save_id: "burn-2024-002",
    title: "Burn Severity Map",
    size_bytes: 2048000,
    created_at: "2024-02-20T14:00:00Z",
    result_path: "/data/results/burn.tif",
    colormap: "rdylgn",
  },
];

const AUTH_TOKEN = "test-token";

function renderPanel(props: Partial<ComponentProps<typeof SavedJobsPanel>> = {}) {
  return render(<SavedJobsPanel authToken={AUTH_TOKEN} {...props} />);
}

describe("SavedJobsPanel", () => {
  let fetchSpy: ReturnType<typeof createSpy>;

  beforeEach(() => {
    resetSpies();
    fetchSpy = installFetchSpy();
    resolveWith(fetchSpy, jsonOk({ jobs: SAMPLE_JOBS }));
  });

  // ---- Rendering ----

  it("renders the Saved Results heading", async () => {
    renderPanel();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText("Saved Results")).toBeInTheDocument();
  });

  it("renders all jobs after initial fetch", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });
    expect(screen.getByText("Burn Severity Map")).toBeInTheDocument();
  });

  it("shows empty state when server returns no jobs", async () => {
    resolveWith(fetchSpy, jsonOk({ jobs: [] }));
    renderPanel();
    await waitFor(() => {
      expect(
        screen.getByText(
          "No saved results yet. Run an analysis to get started.",
        ),
      ).toBeInTheDocument();
    });
  });

  it("shows error state when fetch fails", async () => {
    resolveWith(fetchSpy, jsonFail());
    renderPanel();
    await waitFor(() => {
      expect(
        screen.getByText("Failed to fetch saved jobs"),
      ).toBeInTheDocument();
    });
  });

  it("displays formatted file sizes", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });
    // 1024 bytes = 1 KB
    expect(screen.getByText(/1 KB/)).toBeInTheDocument();
    // 2048000 bytes ~ 1.95 MB
    expect(screen.getByText(/MB/)).toBeInTheDocument();
  });

  // ---- Fetch lifecycle ----

  it("calls the correct endpoint on mount", async () => {
    renderPanel();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/saved-jobs?limit=50", {
        headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
      });
    });
  });

  it("re-fetches when refreshTrigger increments", async () => {
    const { rerender } = renderPanel({ refreshTrigger: 0 });
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });

    rerender(<SavedJobsPanel authToken={AUTH_TOKEN} refreshTrigger={1} />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
  });

  it("re-fetches when the refresh button is clicked", async () => {
    renderPanel();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByTitle("Refresh"));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
  });

  // ---- Delete confirmation flow ----

  it("shows Confirm and Cancel buttons after clicking the delete icon", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });

    const trashButtons = screen
      .getAllByRole("button")
      .filter((btn) => btn.classList.contains("text-destructive"));
    fireEvent.click(trashButtons[0]!);

    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeInTheDocument();
      expect(screen.getByText("Cancel")).toBeInTheDocument();
    });
  });

  it("hides confirmation when Cancel is clicked", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });

    const trashButtons = screen
      .getAllByRole("button")
      .filter((btn) => btn.classList.contains("text-destructive"));
    fireEvent.click(trashButtons[0]!);

    await waitFor(() => {
      expect(screen.getByText("Cancel")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Cancel"));

    await waitFor(() => {
      expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
    });
  });

  it("invokes onDeleteJob and sends DELETE request after confirming", async () => {
    const onDeleteJob = createSpy();
    // First call = job list, second call = DELETE response
    resolveOnce(fetchSpy, jsonOk({ jobs: SAMPLE_JOBS }));
    resolveOnce(fetchSpy, jsonOk({ deleted: true }));

    renderPanel({ onDeleteJob });
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });

    const trashButtons = screen
      .getAllByRole("button")
      .filter((btn) => btn.classList.contains("text-destructive"));
    fireEvent.click(trashButtons[0]!);

    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => {
      expect(onDeleteJob).toHaveBeenCalledWith("ndvi-2024-001");
    });
    expect(fetchSpy).toHaveBeenCalledWith("/saved-jobs/ndvi-2024-001", {
      method: "DELETE",
      headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
    });
  });

  it("removes the deleted job from the rendered list", async () => {
    resolveOnce(fetchSpy, jsonOk({ jobs: SAMPLE_JOBS }));
    resolveOnce(fetchSpy, jsonOk({}));

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });

    const trashButtons = screen
      .getAllByRole("button")
      .filter((btn) => btn.classList.contains("text-destructive"));
    fireEvent.click(trashButtons[0]!);

    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => {
      expect(screen.queryByText("NDVI Analysis")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Burn Severity Map")).toBeInTheDocument();
  });

  // ---- Load on map ----

  it("calls onLoadJob with the correct job when Load on Map is clicked", async () => {
    const onLoadJob = createSpy();
    renderPanel({ onLoadJob });

    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByText("Load on Map")[0]!);
    expect(onLoadJob).toHaveBeenCalledWith(SAMPLE_JOBS[0]);
  });

  it("does not render Load on Map buttons when onLoadJob is not provided", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("NDVI Analysis")).toBeInTheDocument();
    });

    expect(screen.queryByText("Load on Map")).not.toBeInTheDocument();
  });

  it("marks a backed-up result that is missing here and loads it to restore it", async () => {
    const lost = { ...SAMPLE_JOBS[0]!, available: false, restorable: true };
    resolveWith(fetchSpy, jsonOk({ jobs: [lost, { ...SAMPLE_JOBS[1]!, available: true, restorable: true }] }));
    const onLoadJob = createSpy();
    renderPanel({ onLoadJob });

    await waitFor(() => {
      expect(screen.getByText("Restore & load")).toBeInTheDocument();
    });
    expect(screen.getByText("Backed up")).toBeInTheDocument();
    expect(screen.getAllByText("Load on Map")).toHaveLength(1);

    fireEvent.click(screen.getByText("Restore & load"));
    expect(onLoadJob).toHaveBeenCalledWith(lost);
  });
});
