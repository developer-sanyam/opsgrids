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
export { Avatar } from "./components/Avatar";
export type { AvatarSize } from "./components/Avatar";
export { Button } from "./components/Button";
export type { ButtonSize, ButtonVariant } from "./components/Button";
export { Card } from "./components/Card";
export type { CardTone } from "./components/Card";
export { CheckboxGroup } from "./components/CheckboxGroup";
export type { CheckboxOption } from "./components/CheckboxGroup";
export { Combobox } from "./components/Combobox";
export type { ComboboxCopy, ComboboxOption } from "./components/Combobox";
export { ConfirmDialog } from "./components/ConfirmDialog";
export { DataTable } from "./components/DataTable";
export type { Column, TableSelection } from "./components/DataTable";
export { DateRangeField } from "./components/DateRangeField";
export { DateTimeField } from "./components/DateTimeField";
export { DeltaPill } from "./components/DeltaPill";
export { Dialog } from "./components/Dialog";
export { FormDialog } from "./components/FormDialog";
export { FormField } from "./components/FormField";
export type { FieldIds } from "./components/FormField";
export { InlineAlert } from "./components/InlineAlert";
export type { InlineAlertLive, InlineAlertTone } from "./components/InlineAlert";
export { KpiCard } from "./components/KpiCard";
export type { KpiTone } from "./components/KpiCard";
export { LoadMore } from "./components/LoadMore";
export { PageHeader } from "./components/PageHeader";
export { Pagination } from "./components/Pagination";
export { Pill } from "./components/Pill";
export { QrCode } from "./components/QrCode";
export { RadioGroup } from "./components/RadioGroup";
export type { RadioOption } from "./components/RadioGroup";
export { RowActions } from "./components/RowActions";
export type { RowAction } from "./components/RowActions";
export { SampleChip } from "./components/SampleChip";
export { SearchInput } from "./components/SearchInput";
export { Select } from "./components/Select";
export type { SelectOption } from "./components/Select";
export { Sidebar } from "./components/Sidebar";
export type { NavEntry, NavGroup, SidebarAccount } from "./components/Sidebar";
export { Sparkline } from "./components/Sparkline";
export { EmptyState, ErrorState, SectionHeader, Skeleton } from "./components/States";
export type { ErrorStateTone } from "./components/States";
export { StatusBadge } from "./components/StatusBadge";
export type { StatusBadgeProps } from "./components/StatusBadge";
export { isStatusWord, STATUS_TONES, STATUS_WORDS, statusLabel, statusTone } from "./lib/status";
export type { StatusTone, StatusWord } from "./lib/status";
export { Switch } from "./components/Switch";
export { Tag } from "./components/Tag";
export type { TagTone } from "./components/Tag";
export { TextArea } from "./components/TextArea";
export { TextField } from "./components/TextField";
export { ThemeProvider } from "./components/ThemeProvider";
export { ThemeToggle } from "./components/ThemeToggle";
export { Toaster } from "./components/Toaster";
export { Timeline } from "./components/Timeline";
export type { TimelineEntry, TimelineTone } from "./components/Timeline";

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
export { formatDelta, initials } from "./lib/format";
export { encodeQr, qrPathData } from "./lib/qr";
export type { EncodeOptions, QrLevel, QrMatrix } from "./lib/qr";
export { useTheme } from "./lib/theme";
export type { Theme, ThemeControls } from "./lib/theme";
export { useToast } from "./lib/toast";
export type { ToastControls, ToastOptions, ToastTone } from "./lib/toast";

export * from "./icons";
