import type {
  TemplateButtonType,
  TemplateCategory,
  TemplateHistoryEntry,
  TemplateHistorySource,
  TemplateQuality,
  TemplateVariableKind,
  WhatsAppTemplate,
} from "@/api/whatsappTemplates";

/** The words this feature uses for the wire's tokens, each in one place. */

export const CATEGORY_LABEL: Record<TemplateCategory, string> = {
  utility: "Utility",
  marketing: "Marketing",
  authentication: "Authentication",
};

/** The languages the create flow offers, by Meta's language code. */
export const LANGUAGES: readonly { value: string; label: string }[] = [
  { value: "en_US", label: "English (US)" },
  { value: "en_GB", label: "English (UK)" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
];

/** A code the list does not know is shown as it came rather than hidden. */
export function languageLabel(code: string): string {
  return LANGUAGES.find((language) => language.value === code)?.label ?? code;
}

export const QUALITY_LABEL: Record<TemplateQuality, string> = {
  high: "High quality",
  medium: "Medium quality",
  low: "Low quality",
  unknown: "Quality not rated",
};

export const VARIABLE_KIND_LABEL: Record<TemplateVariableKind, string> = {
  text: "Text",
  url: "URL",
  date: "Date",
  number: "Number",
};

export const BUTTON_TYPE_LABEL: Record<TemplateButtonType, string> = {
  quick_reply: "Quick reply",
  url: "Opens a link",
  phone: "Calls a number",
};

/** The template's display name: `name` if the service sends one, else the identifier it is filed under. */
export function templateName(template: Pick<WhatsAppTemplate, "id" | "name">): string {
  return template.name ?? template.id;
}

const SOURCE_LABEL: Record<TemplateHistorySource, string> = {
  meta_webhook: "Meta webhook",
  sync: "Sync",
  submit: "Submitted here",
};

/** One line of the approval history, in the words a person would say it. */
export function historyLabel(entry: TemplateHistoryEntry): string {
  switch (entry.status) {
    case "draft":
      return "Saved as draft";
    case "pending":
      return entry.source === "submit" ? "Submitted to Meta" : "Pending Meta review";
    case "approved":
      return "Approved by Meta";
    case "rejected":
      return "Rejected by Meta";
    case "paused":
      return "Paused by Meta";
    case "disabled":
      return "Disabled";
  }
}

export function historySourceLabel(source: TemplateHistorySource): string {
  return SOURCE_LABEL[source];
}
