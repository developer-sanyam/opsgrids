/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Avatar,
  Button,
  Card,
  Combobox,
  ConfirmDialog,
  DataTable,
  DeltaPill,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  KpiCard,
  PageHeader,
  Pagination,
  Pill,
  RadioGroup,
  RowActions,
  SampleChip,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  Tag,
  TextArea,
  TextField,
} from "@opsgrids/ui";
export type { Column, ComboboxOption, KpiTone, RadioOption, RowAction, SelectOption } from "@opsgrids/ui";

export { Sidebar } from "./Sidebar";
export { SpendTrend } from "./SpendTrend";
export { CategoryBreakdown } from "./CategoryBreakdown";
export { StatusMix } from "./StatusMix";
export type { MiniStat } from "./StatusMix";
export { NeedsAttention } from "./NeedsAttention";
export { TopSuppliers } from "./TopSuppliers";
export { PoStatusPill, SupplierStatusPill, StockPill } from "./StatusPill";
export type { StockLevel } from "./StatusPill";
