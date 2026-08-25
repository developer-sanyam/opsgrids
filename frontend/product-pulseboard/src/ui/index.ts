/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@atmora/ui` as a variant (ADR-0001).
 */

export {
  Avatar,
  Button,
  Card,
  DataTable,
  DeltaPill,
  EmptyState,
  ErrorState,
  KpiCard,
  PageHeader,
  Pill,
  SearchInput,
  SectionHeader,
  Skeleton,
  Tag,
} from "@atmora/ui";
export type { Column, KpiTone } from "@atmora/ui";

export { Sidebar } from "./Sidebar";
export { ProjectStatusPill, PriorityPill } from "./StatusPill";
export { PROJECT_STATUS_META, TASK_STATUS_META } from "./statusMeta";
export { UtilizationTrend } from "./UtilizationTrend";
export { ProjectHealth } from "./ProjectHealth";
export { NeedsAttention } from "./NeedsAttention";
export { KanbanBoard } from "./KanbanBoard";
export { TimeGrid } from "./TimeGrid";
export { StandupList } from "./StandupList";
