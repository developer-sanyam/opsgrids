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
  CheckboxGroup,
  Combobox,
  ConfirmDialog,
  DataTable,
  DeltaPill,
  DonutChart,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  KpiCard,
  LineChart,
  MetricCard,
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
  Switch,
  Tag,
  TextArea,
  TextField,
} from "@opsgrids/ui";
export type {
  BarListItem,
  CheckboxOption,
  Column,
  ComboboxOption,
  DonutSlice,
  KpiTone,
  RadioOption,
  RowAction,
  SelectOption,
} from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { NeedsAttention } from "./NeedsAttention";
export { PoStatusPill, SupplierStatusPill, StockPill } from "./StatusPill";
export type { StockLevel } from "./StatusPill";
