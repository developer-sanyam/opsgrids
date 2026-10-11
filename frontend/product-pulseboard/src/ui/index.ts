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
  SearchInput,
  SectionHeader,
  Skeleton,
  Tag,
} from "@opsgrids/ui";
export type { Column, KpiTone } from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { ProjectStatusPill, PriorityPill } from "./StatusPill";
export { PROJECT_STATUS_META, TASK_STATUS_META } from "./statusMeta";
export { UtilizationTrend } from "./UtilizationTrend";
export { ProjectHealth } from "./ProjectHealth";
export { NeedsAttention } from "./NeedsAttention";
export { TimeGrid } from "./TimeGrid";
export { StandupList } from "./StandupList";
