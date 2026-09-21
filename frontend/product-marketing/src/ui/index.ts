/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  FormDialog,
  FormField,
  InlineAlert,
  KpiCard,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  TextArea,
  TextField,
} from "@opsgrids/ui";
export type { Column, FieldIds, KpiTone, RowAction, SelectOption } from "@opsgrids/ui";

export { Sidebar } from "./Sidebar";
export { CampaignStatusPill } from "./StatusPill";
export { ChannelBadge } from "./ChannelBadge";
export { CHANNEL_COLOR, CHANNEL_ICON, CHANNEL_TONE } from "./channelMeta";
export { BudgetBar } from "./BudgetBar";
export { StatTile } from "./StatTile";
export { RevenueSpendChart } from "./RevenueSpendChart";
export { ChannelBreakdown } from "./ChannelBreakdown";
export { NeedsAttention } from "./NeedsAttention";
export { CampaignPerformanceChart } from "./CampaignPerformanceChart";
export type { WeekPoint } from "./CampaignPerformanceChart";
