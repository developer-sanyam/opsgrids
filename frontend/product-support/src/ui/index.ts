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
  EmptyState,
  ErrorState,
  KpiCard,
  PageHeader,
  SampleChip,
  SearchInput,
  SectionHeader,
  Skeleton,
  Tag,
} from "@atmora/ui";
export type { Column, KpiTone } from "@atmora/ui";

export { Sidebar } from "./Sidebar";
export { PriorityPill, TicketStatusPill } from "./StatusPill";
export { SlaPill } from "./SlaPill";
export { TicketVolumeChart } from "./TicketVolumeChart";
export { CategoryBreakdown } from "./CategoryBreakdown";
