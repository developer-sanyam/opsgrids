/**
 * Status → presentation maps, kept in a plain module (no component exports) so both the pills and
 * the charts can share one source of truth for labels and colours without tripping Fast Refresh.
 */
import type { ProjectStatus, TaskStatus } from "@/api/types";

/**
 * Project health mapped to the semantic pill families:
 * On track = green, At risk = amber, Blocked = red, Delivered = blue.
 * A CSS-variable colour is exposed too, so charts and bars stay in step with the pills.
 */
export const PROJECT_STATUS_META: Record<
  ProjectStatus,
  { label: string; tone: string; color: string }
> = {
  on_track: { label: "On track", tone: "bg-present-soft text-present", color: "var(--color-present)" },
  at_risk: { label: "At risk", tone: "bg-late-soft text-late", color: "var(--color-late)" },
  blocked: { label: "Blocked", tone: "bg-danger-soft text-danger", color: "var(--color-danger)" },
  done: { label: "Delivered", tone: "bg-info-soft text-info", color: "var(--color-info)" },
};

export const TASK_STATUS_META: Record<TaskStatus, { label: string; color: string }> = {
  backlog: { label: "Backlog", color: "var(--color-faint)" },
  in_progress: { label: "In Progress", color: "var(--color-accent)" },
  review: { label: "Review", color: "var(--color-late)" },
  done: { label: "Done", color: "var(--color-present)" },
};
