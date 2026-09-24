/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense in the operator console. A component below is one nothing else in the suite would render —
 * anything general belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Avatar,
  Button,
  Card,
  DataTable,
  DefinitionList,
  DeltaPill,
  Dialog,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  KpiCard,
  PageHeader,
  Pill,
  RadioGroup,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  Tag,
  TextArea,
  TextField,
} from "@opsgrids/ui";
export type { Column, Definition, KpiTone, RadioOption, SelectOption } from "@opsgrids/ui";

export { BreakdownBar } from "./BreakdownBar";
export { EnvBadge } from "./EnvBadge";
export { LoadFailure } from "./LoadFailure";
export { LogoMark } from "./LogoMark";
export { Money } from "./Money";
export { MrrTrend } from "./MrrTrend";
export { PermissionDenied, RequireCapability } from "./PermissionDenied";
export { Sidebar } from "./Sidebar";
export {
  AccountStatusPill,
  BespokeTag,
  CompTag,
  InvoiceStatePill,
  LedgerKindPill,
  PlanVersionPill,
  StatusPill,
} from "./StatusPill";
export type { PillTone } from "./StatusPill";
