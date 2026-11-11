import { describe, it, expect } from "vitest";
import { settleWorkflow } from "../workflow";
import type { Workflow } from "@/types";

function workflow(status: Workflow["status"]): Workflow {
  return { id: "w", name: "w", status, steps: [], createdAt: new Date() };
}

describe("settleWorkflow", () => {
  it("settles a running workflow", () => {
    expect(settleWorkflow(workflow("running"), "completed")?.status).toBe("completed");
    expect(settleWorkflow(workflow("running"), "stopped")?.status).toBe("stopped");
  });

  it("keeps an error or a stop when the turn's done arrives", () => {
    expect(settleWorkflow(workflow("error"), "completed")?.status).toBe("error");
    expect(settleWorkflow(workflow("stopped"), "completed")?.status).toBe("stopped");
  });

  it("leaves no workflow alone", () => {
    expect(settleWorkflow(null, "completed")).toBeNull();
  });
});
