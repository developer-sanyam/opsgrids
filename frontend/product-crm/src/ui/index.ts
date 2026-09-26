/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Avatar,
  BarList,
  Button,
  Card,
  CardSkeleton,
  ChartCard,
  ConfirmDialog,
  DataTable,
  DonutChart,
  EmptyState,
  ErrorState,
  InlineAlert,
  LoadMore,
  MetricCard,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  Switch,
  TABLET_QUERY,
  useMediaQuery,
} from "@opsgrids/ui";
export type {
  BarListItem,
  Column,
  DonutSlice,
  KpiTone,
  MetricTrend,
  RowAction,
  TableSelection,
} from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { CompanyStatusPill, ContactStatusPill, LeadStatusPill, StagePill } from "./StatusPill";
export { OwnerBadge } from "./OwnerBadge";
export { ActivityTimeline } from "./ActivityTimeline";
export { NeedsAttention } from "./NeedsAttention";
export { KanbanBoard } from "./KanbanBoard";
