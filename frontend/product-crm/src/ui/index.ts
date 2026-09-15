/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
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
  SampleChip,
  SearchInput,
  SectionHeader,
  Skeleton,
} from "@opsgrids/ui";
export type { Column, KpiTone } from "@opsgrids/ui";

export { Sidebar } from "./Sidebar";
export { CompanyStatusPill, ContactStatusPill, StagePill } from "./StatusPill";
export { OwnerBadge } from "./OwnerBadge";
export { ActivityTimeline } from "./ActivityTimeline";
export { PipelineTrend } from "./PipelineTrend";
export { PipelineFunnel } from "./PipelineFunnel";
export { NeedsAttention } from "./NeedsAttention";
export { KanbanBoard } from "./KanbanBoard";
