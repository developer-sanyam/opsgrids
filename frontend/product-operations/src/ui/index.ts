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
  Drawer,
  EmptyState,
  ErrorState,
  FormDialog,
  FormSection,
  InlineAlert,
  KpiCard,
  LineChart,
  MetricCard,
  PageHeader,
  Pagination,
  Pill,
  QrCode,
  RadioGroup,
  RowActions,
  SampleChip,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  Stepper,
  StepperActions,
  Switch,
  Tabs,
  Tag,
  TextArea,
  TextField,
  UnsavedChangesGuard,
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
  StepItem,
  TabItem,
  TableSelection,
} from "@opsgrids/ui";

export { Barcode128 } from "./Barcode128";
export { useNavGroups } from "./useNavGroups";
export { LoadError } from "./LoadError";
export { NeedsAttention } from "./NeedsAttention";
export {
  AdjustmentStatusPill,
  ApprovalRouteChip,
  ReservationStatusPill,
  TransferStatusPill,
  AssignmentKindPill,
  AssignmentOfferStatusPill,
  BomVersionStatusPill,
  ChannelStatusPill,
  ChannelSyncStatusPill,
  CollectionStatusPill,
  CountLineReviewStatusPill,
  CountSessionStatusPill,
  InspectionStatusPill,
  MrpRunStatusPill,
  OrderStatusPill,
  PickTaskStatusPill,
  PoStatusPill,
  ReceiptOutcomePill,
  ProductionOrderStatusPill,
  RequisitionStatusPill,
  ShipmentStatusPill,
  SupplierStatusPill,
  StockPill,
  StockStatusPill,
} from "./StatusPill";
export type { StockLevel } from "./StatusPill";
