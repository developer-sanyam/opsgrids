import { StatusBadge } from "@opsgrids/ui";

import type { AutomationStatus, RunState, StepState } from "@/api/automations";
import { RUN_LABEL, STEP_LABEL } from "./labels";

/** The status pills the automation screens share, each state in one place. `draft`, `active` and
 *  `paused` are registered status words with the platform's tones. */

export function AutomationStatusTag({ status }: { status: AutomationStatus }) {
  return <StatusBadge status={status} />;
}

export function RunStateTag({ state }: { state: RunState }) {
  const { label, tone } = RUN_LABEL[state];
  return <StatusBadge tone={tone} label={label} />;
}

export function StepStateTag({ state }: { state: StepState }) {
  const { label, tone } = STEP_LABEL[state];
  return <StatusBadge tone={tone} label={label} />;
}
