// ABOUTME: The chat workflow's status moves. A workflow settles once: a done arriving after
// ABOUTME: an error or a stop must not relabel it as completed.
import type { Workflow } from "@/types";

export type WorkflowOutcome = "completed" | "error" | "stopped";

/** Settle a running workflow with outcome; a workflow that has already settled keeps its status. */
export function settleWorkflow(workflow: Workflow | null, outcome: WorkflowOutcome): Workflow | null {
  if (!workflow || workflow.status !== "running") return workflow;
  return { ...workflow, status: outcome, completedAt: new Date() };
}
