/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 *
 * Note that nothing under `src/theme/render` imports from here. Those components draw the
 * *buyer's* surface, whose palette is merchant data; the Console primitives below draw the
 * merchant's admin chrome around it. Keeping the two sets apart is deliberate.
 */

export {
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pill,
  SampleChip,
  SectionHeader,
  Skeleton,
  Tag,
} from "@opsgrids/ui";
export type { TagTone } from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { LogoMark } from "./LogoMark";
export { DeviceToggle } from "./DeviceToggle";
export { DEVICE_WIDTHS } from "./deviceWidths";
export type { DeviceKey } from "./deviceWidths";
export { ThemePreview } from "./ThemePreview";
