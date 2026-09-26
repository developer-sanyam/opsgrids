/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */

export {
  Announcer,
  Button,
  Card,
  Combobox,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  FormSection,
  InlineAlert,
  PageHeader,
  Pagination,
  RadioGroup,
  RowActions,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  StatusBadge,
  Tag,
  TextArea,
  TextField,
  useAnnounce,
  useDebouncedValue,
} from "@opsgrids/ui";
export type { Column, ComboboxOption, RadioOption, SelectOption, TableSelection, TagTone } from "@opsgrids/ui";

export { useNavGroups } from "./useNavGroups";
export { LogoMark } from "./LogoMark";
export {
  CampaignStatusTag,
  ConnectionStatusTag,
  ConversationStatusTag,
  DomainStatusTag,
  RecipientStatusTag,
  SenderStatusTag,
  SuppressionReasonTag,
  TemplateStatusTag,
  WindowStatusTag,
} from "./StatusTags";
export { EngageNotActive, LoadFailure } from "./LoadFailure";
export { PageHeading } from "./PageHeading";
