import type { StatusTone } from "@opsgrids/ui";

import type { RunState, StepState } from "@/api/automations";

/** The words for a run's and a step's state, with the tone each carries. Every label says the
 *  state in words, so colour is never the only signal. */

export const RUN_LABEL: Record<RunState, { label: string; tone: StatusTone }> = {
  running: { label: "Running", tone: "info" },
  waiting: { label: "Waiting", tone: "warning" },
  completed: { label: "Completed", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  skipped: { label: "Skipped", tone: "neutral" },
};

export const RUN_STATES: RunState[] = ["running", "waiting", "completed", "failed", "skipped"];

export function runStateLabel(state: RunState): string {
  return RUN_LABEL[state].label;
}

export const STEP_LABEL: Record<StepState, { label: string; tone: StatusTone }> = {
  waiting: { label: "Waiting", tone: "warning" },
  running: { label: "Running", tone: "info" },
  done: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  skipped: { label: "Skipped", tone: "neutral" },
};

export function stepStateLabel(state: StepState): string {
  return STEP_LABEL[state].label;
}
