/**
 * The Atmora design system. One import for every app: the components, the icons and the two
 * helpers those components need.
 *
 * The stylesheet is a separate entry — `@import "@atmora/ui/styles.css";` after Tailwind, in the
 * app's own `index.css`.
 *
 * An app may not fork a component into itself (ADR-0001). If a product needs a variant, it lands
 * here as a prop.
 */
export { AppShell } from "./components/AppShell";
export { Avatar } from "./components/Avatar";
export type { AvatarSize } from "./components/Avatar";
export { Button } from "./components/Button";
export type { ButtonSize, ButtonVariant } from "./components/Button";
export { Card } from "./components/Card";
export type { CardTone } from "./components/Card";
export { DataTable } from "./components/DataTable";
export type { Column } from "./components/DataTable";
export { DeltaPill } from "./components/DeltaPill";
export { KpiCard } from "./components/KpiCard";
export type { KpiTone } from "./components/KpiCard";
export { PageHeader } from "./components/PageHeader";
export { Pill } from "./components/Pill";
export { SampleChip } from "./components/SampleChip";
export { SearchInput } from "./components/SearchInput";
export { Sidebar } from "./components/Sidebar";
export type { NavEntry, NavGroup, SidebarAccount } from "./components/Sidebar";
export { Sparkline } from "./components/Sparkline";
export { EmptyState, ErrorState, SectionHeader, Skeleton } from "./components/States";
export { Tag } from "./components/Tag";
export { ThemeProvider } from "./components/ThemeProvider";
export { ThemeToggle } from "./components/ThemeToggle";

export { cn } from "./lib/cn";
export { formatDelta, initials } from "./lib/format";
export { useTheme } from "./lib/theme";
export type { Theme, ThemeControls } from "./lib/theme";

export * from "./icons";
