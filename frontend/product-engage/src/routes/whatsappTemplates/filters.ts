import type { TemplateCategory, TemplateStatus, TemplateSyncResult } from "@/api/whatsappTemplates";

/** The list's filter state and the two small decisions made from it, apart from the component. */

export type CategoryFilter = "" | TemplateCategory;
export type StatusFilter = "" | TemplateStatus;
export interface Filters {
  category: CategoryFilter;
  status: StatusFilter;
}

/** The tab that stands for exactly this filter pair, or `""` when none does. */
export function tabFor({ category, status }: Filters): string {
  if (category === "" && status === "") return "all";
  if (status === "") return category;
  if (category === "") return status;
  return "";
}

/** "3 new, 8 updated, 1 removed." */
export function syncSummary(result: TemplateSyncResult): { title: string; description: string } {
  const noun = result.synced === 1 ? "template" : "templates";
  return {
    title: `${result.synced} ${noun} synced from Meta`,
    description: `${result.created} new, ${result.updated} updated, ${result.removed} removed.`,
  };
}
