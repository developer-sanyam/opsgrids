/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Announcer,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  RadioGroup,
  SectionHeader,
  Select,
  Skeleton,
  Tag,
  TextField,
  useAnnounce,
} from "@opsgrids/ui";
export type { RadioOption, SelectOption, TagTone } from "@opsgrids/ui";

export { Sidebar } from "./Sidebar";
export { LogoMark } from "./LogoMark";
export {
  ConnectionStatusTag,
  DomainStatusTag,
  SenderStatusTag,
  TemplateStatusTag,
} from "./StatusTags";
export { EngageNotActive, LoadFailure, NotificationNotActive } from "./LoadFailure";
