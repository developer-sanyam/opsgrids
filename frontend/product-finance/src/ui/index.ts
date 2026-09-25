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
  Switch,
  Tag,
  TextArea,
  TextField,
  Timeline,
} from "@opsgrids/ui";
export type {
  Column,
  KpiTone,
  RadioOption,
  RowAction,
  SelectOption,
  TableSelection,
  TimelineEntry,
  TimelineTone,
} from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { CashflowChart } from "./CashflowChart";
export { AgingBreakdown } from "./AgingBreakdown";
export { InvoiceStatusPill, PaymentMethodBadge } from "./StatusPill";
export {
  FilingStatusPill,
  ImsStatusPill,
  SampleDataChip,
  SuggestedAction,
} from "./ComplianceStatus";
export { CompliancePanel } from "./CompliancePanel";
export { ComplianceCard } from "./ComplianceCard";
