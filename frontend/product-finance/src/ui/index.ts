/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Avatar,
  Button,
  Card,
  ChartCard,
  ConfirmDialog,
  DataTable,
  DonutChart,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  isStatusWord,
  KpiCard,
  LineChart,
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
  StatusBadge,
  statusLabel,
  Switch,
  TABLET_QUERY,
  Tag,
  TextArea,
  TextField,
  Timeline,
  UnsavedChangesGuard,
  useFormDirty,
  useMediaQuery,
} from "@opsgrids/ui";
export type {
  ChartSeries,
  ChartTone,
  Column,
  DonutSlice,
  KpiTone,
  RadioOption,
  RowAction,
  SelectOption,
  TableSelection,
  TimelineEntry,
  TimelineTone,
} from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { AgingBreakdown } from "./AgingBreakdown";
export { InvoiceStatusPill, PaymentMethodBadge } from "./StatusPill";
export {
  FilingStatusPill,
  GST_PORTAL_CONNECTED,
  ImsStatusPill,
  PortalSimulatedNotice,
  SampleDataChip,
  SuggestedAction,
} from "./ComplianceStatus";
export { CompliancePanel } from "./CompliancePanel";
export { ComplianceCard } from "./ComplianceCard";
export { Explainer } from "./Explainer";
