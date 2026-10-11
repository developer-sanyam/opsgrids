/**
 * The OpsGrids design system. One import for every app: the components, the icons and the two
 * helpers those components need.
 *
 * The stylesheet is a separate entry — `@import "@opsgrids/ui/styles.css";` after Tailwind, in the
 * app's own `index.css`.
 *
 * An app may not fork a component into itself (ADR-0001). If a product needs a variant, it lands
 * here as a prop.
 */
export { Announcer } from "./components/Announcer";
export { AppShell } from "./components/AppShell";
export type {
  AppShellProduct,
  AppShellProps,
  AppShellUser,
  CommandMenuConfig,
  QuickAction,
} from "./components/AppShell";
export { Avatar } from "./components/Avatar";
export type { AvatarSize } from "./components/Avatar";
export { BarChart } from "./components/BarChart";
export { BarList } from "./components/BarList";
export type { BarListItem } from "./components/BarList";
export { BottomNavigation } from "./components/BottomNavigation";
export type { BottomNavItem } from "./components/BottomNavigation";
export { BulkActionBar } from "./components/BulkActionBar";
export { Button } from "./components/Button";
export type { ButtonSize, ButtonVariant } from "./components/Button";
export { Card } from "./components/Card";
export type { CardTone } from "./components/Card";
export { ChartCard } from "./components/ChartCard";
export { CheckboxGroup } from "./components/CheckboxGroup";
export type { CheckboxOption } from "./components/CheckboxGroup";
export { Combobox } from "./components/Combobox";
export type { ComboboxCopy, ComboboxOption } from "./components/Combobox";
export { CommandMenu } from "./components/CommandMenu";
export type { CommandMenuItem, CommandMenuResults } from "./components/CommandMenu";
export { ConfirmDialog } from "./components/ConfirmDialog";
export { CopyField } from "./components/CopyField";
export { DataTable } from "./components/DataTable";
export type { Column, TableSelection } from "./components/DataTable";
export { DateRangeField } from "./components/DateRangeField";
export { DateTimeField } from "./components/DateTimeField";
export { DeltaPill } from "./components/DeltaPill";
export { Dialog } from "./components/Dialog";
export { DonutChart } from "./components/DonutChart";
export type { DonutSlice } from "./components/DonutChart";
export { Drawer } from "./components/Drawer";
export { FileUpload } from "./components/FileUpload";
export { ImportExportButton } from "./components/ImportExportButton";
export type {
  ImportExportBaselineUse,
  ImportExportButtonProps,
  ImportExportMeta,
  ImportExportMode,
  ImportExportPhase,
  ImportExportPreview,
  ImportExportPreviewAction,
  ImportExportPreviewRow,
  ImportExportProgress,
  ImportExportRowOutcome,
  ImportExportRowStatus,
  ImportExportSummary,
  ImportExportViewActions,
  ImportExportViewState,
} from "./components/ImportExportButton";
export type { StoredFile, UploadControls } from "./components/FileUpload";
export { FilterBar } from "./components/FilterBar";
export type { FilterChip, FilterSearch, FilterSelect } from "./components/FilterBar";
export { FormDialog } from "./components/FormDialog";
export { FormField } from "./components/FormField";
export type { FieldIds } from "./components/FormField";
export { FormSection } from "./components/FormSection";
export { HeroMetricCard } from "./components/HeroMetricCard";
export { InlineAlert } from "./components/InlineAlert";
export type { InlineAlertLive, InlineAlertTone } from "./components/InlineAlert";
export { KpiCard } from "./components/KpiCard";
export type { KpiTone } from "./components/KpiCard";
export { LineChart } from "./components/LineChart";
export type { ChartSeries, ChartTone } from "./components/LineChart";
export { LoadMore } from "./components/LoadMore";
export { Menu } from "./components/Menu";
export type { MenuItem } from "./components/Menu";
export { MetricCard } from "./components/MetricCard";
export type { MetricTrend } from "./components/MetricCard";
export { MobileDataCard } from "./components/MobileDataCard";
export { MobileHeader } from "./components/MobileHeader";
export type { MobileCardSelection, MobileCardWiring, MobileDataField } from "./components/MobileDataCard";
export { PageHeader } from "./components/PageHeader";
export { Pagination } from "./components/Pagination";
export { Pill } from "./components/Pill";
export { QrCode } from "./components/QrCode";
export { RadioGroup } from "./components/RadioGroup";
export type { RadioOption } from "./components/RadioGroup";
export { RowActions } from "./components/RowActions";
export type { RowAction } from "./components/RowActions";
export { Customer360View } from "./components/Customer360View";
export type { Customer360Money, Customer360ViewModel } from "./components/Customer360View";
export { DetailList, PropertyRow } from "./components/DetailList";
export type { DetailItem } from "./components/DetailList";
export { SavedViewMenu } from "./components/SavedViewMenu";
export type { SavedViewItem, SavedViewMenuProps, SavedViewVisibility } from "./components/SavedViewMenu";
export { KanbanBoard } from "./components/KanbanBoard";
export type { KanbanBoardProps, KanbanCardState, KanbanColumn, KanbanMoveResult } from "./components/KanbanBoard";
export { SampleChip } from "./components/SampleChip";
export { SearchInput } from "./components/SearchInput";
export { Select } from "./components/Select";
export type { SelectOption } from "./components/Select";
export { Sidebar } from "./components/Sidebar";
export type { ModuleLink, NavEntry, NavGroup, SidebarUser } from "./components/Sidebar";
export {
  CardSkeleton,
  ChartSkeleton,
  DashboardSkeleton,
  FormSkeleton,
  TableSkeleton,
} from "./components/Skeletons";
export { Sparkline } from "./components/Sparkline";
export { EmptyState, ErrorState, SectionHeader, Skeleton } from "./components/States";
export type { ErrorStateTone } from "./components/States";
export { StatusBadge } from "./components/StatusBadge";
export type { StatusBadgeProps } from "./components/StatusBadge";
export { isStatusWord, STATUS_TONES, STATUS_WORDS, statusLabel, statusTone } from "./lib/status";
export type { StatusTone, StatusWord } from "./lib/status";
export { StepUpDialog } from "./components/StepUpDialog";
export type { StepUpDialogError, StepUpDialogProps, StepUpMfaState } from "./components/StepUpDialog";
export { Stepper, StepperActions } from "./components/Stepper";
export type { StepItem } from "./components/Stepper";
export { Switch } from "./components/Switch";
export { Tabs } from "./components/Tabs";
export type { TabItem, TabsVariant } from "./components/Tabs";
export { Tag } from "./components/Tag";
export type { TagTone } from "./components/Tag";
export { TextArea } from "./components/TextArea";
export { TextField } from "./components/TextField";
export { ThemeProvider } from "./components/ThemeProvider";
export { ThemeToggle } from "./components/ThemeToggle";
export { Toaster } from "./components/Toaster";
export { Timeline } from "./components/Timeline";
export type { TimelineEntry, TimelineTone } from "./components/Timeline";
export { UnsavedChangesGuard } from "./components/UnsavedChangesGuard";

export { useAnnounce } from "./lib/announcer";
export type { Announce } from "./lib/announcer";
export { cn } from "./lib/cn";
export {
  formatDuration,
  formatInstant,
  formatInZone,
  formatRelative,
  instantToWallClock,
  resolveZone,
  wallClockToInstant,
  zoneAbbreviation,
} from "./lib/datetime";
export type {
  DateTimeStyle,
  FormattedInstant,
  ZoneLookup,
  ZoneResolution,
} from "./lib/datetime";
export { currencyExponent, formatDelta, formatMoney, initials, minorToInput, parseMinor, toMajor } from "./lib/format";
export type { MoneyOptions, MoneyParse } from "./lib/format";
export { moduleIcon } from "./lib/moduleIcons";
export { encodeQr, qrPathData } from "./lib/qr";
export type { EncodeOptions, QrLevel, QrMatrix } from "./lib/qr";
export { useTheme } from "./lib/theme";
export type { Theme, ThemeControls } from "./lib/theme";
export { useToast } from "./lib/toast";
export type { ToastControls, ToastOptions, ToastTone } from "./lib/toast";
export { ICON_TILE } from "./lib/tones";
export type { Tone } from "./lib/tones";
export { useDebouncedValue } from "./lib/useDebouncedValue";
export { useFormDirty } from "./lib/useFormDirty";
export { TABLET_QUERY, useMediaQuery } from "./lib/useMediaQuery";
export { useUnsavedChangesWarning } from "./lib/useUnsavedChangesWarning";

export * from "./icons";
