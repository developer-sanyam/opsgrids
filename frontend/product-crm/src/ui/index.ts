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
  EmptyState,
  ErrorState,
  InlineAlert,
  KpiCard,
  LoadMore,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SampleChip,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
} from "@opsgrids/ui";
export type { Column, KpiTone, RowAction, TableSelection } from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { CompanyStatusPill, ContactStatusPill, LeadStatusPill, StagePill } from "./StatusPill";
export { OwnerBadge } from "./OwnerBadge";
export { ActivityTimeline } from "./ActivityTimeline";
export { PipelineTrend } from "./PipelineTrend";
export { PipelineFunnel } from "./PipelineFunnel";
export { NeedsAttention } from "./NeedsAttention";
export { KanbanBoard } from "./KanbanBoard";
