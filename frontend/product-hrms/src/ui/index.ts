/**
 * This app's UI barrel: the shared design system, re-exported, plus the widgets that only make
 * sense here. A component below is one nothing else in the suite would render — anything general
 * belongs in `@opsgrids/ui` as a variant (ADR-0001).
 */
export {
  Avatar,
  Button,
  Card,
  DataTable,
  DeltaPill,
  EmptyState,
  ErrorState,
  KpiCard,
  PageHeader,
  Pill,
  SampleChip,
  SearchInput,
  SectionHeader,
  Skeleton,
  Tag,
} from "@opsgrids/ui";
export type { Column, KpiTone } from "@opsgrids/ui";

export { Sidebar } from "./Sidebar";
export { HeadcountByDepartment } from "./HeadcountByDepartment";
export { HeadcountTrend } from "./HeadcountTrend";
export { WorkforceMix } from "./WorkforceMix";
export type { MiniStat } from "./WorkforceMix";
export { EmployeesPanel } from "./EmployeesPanel";
export { AttendancePill, EmployeeStatusPill } from "./StatusPill";
