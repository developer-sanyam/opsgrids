/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  DeltaPill,
  Dialog,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  KpiCard,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SampleChip,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  TextArea,
  TextField,
} from "@opsgrids/ui";
export type { Column, KpiTone, RowAction, SelectOption } from "@opsgrids/ui";

export { Sidebar } from "./Sidebar";
export { StatCard } from "./StatCard";
export {
  HealthDot,
  HealthPill,
  MilestoneStatusPill,
  PhasePill,
  PriorityPill,
  ProgressBar,
} from "./ProjectPills";
export { healthColor } from "./healthColor";
export { Burndown } from "./Burndown";
export { HealthBreakdown } from "./HealthBreakdown";
export { NeedsAttention } from "./NeedsAttention";
export { KanbanBoard } from "./KanbanBoard";
export { GanttTimeline } from "./GanttTimeline";
