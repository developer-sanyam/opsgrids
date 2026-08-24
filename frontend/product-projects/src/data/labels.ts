/**
 * Human labels for the product's enums. One source of truth so a pill, a table cell and a filter
 * chip never disagree on how a phase or a status is spelled.
 */
import type {
  MilestoneStatus,
  ProjectHealth,
  ProjectPhase,
  TaskPriority,
  TaskStage,
} from "@/api/types";

export const PHASE_LABEL: Record<ProjectPhase, string> = {
  discovery: "Discovery",
  design: "Design",
  build: "Build",
  stabilize: "Stabilize",
  launch: "Launch",
  live: "Live",
};

export const HEALTH_LABEL: Record<ProjectHealth, string> = {
  on_track: "On track",
  at_risk: "At risk",
  delayed: "Delayed",
};

export const MILESTONE_STATUS_LABEL: Record<MilestoneStatus, string> = {
  completed: "Completed",
  on_track: "On track",
  due_soon: "Due soon",
  at_risk: "At risk",
  slipping: "Slipping",
};

export const STAGE_LABEL: Record<TaskStage, string> = {
  backlog: "Backlog",
  in_progress: "In Progress",
  review: "Review",
  done: "Done",
};

export const PRIORITY_LABEL: Record<TaskPriority, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  urgent: "Urgent",
};
