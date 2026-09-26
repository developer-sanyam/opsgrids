import type { TriggerVariable } from "@/api/automations";

/** The template body with each `{{key}}` replaced by what it is mapped to, so a person sees the
 *  message they are building and not just a table of paths. An unmapped variable stays as written,
 *  which is also how it will read if it is left that way. */
export function previewText(
  body: string,
  variableMap: Record<string, string>,
  variables: TriggerVariable[],
): string {
  return body.replace(/\{\{\s*([^}\s]+)\s*\}\}/g, (whole, key: string) => {
    const path = variableMap[key];
    if (path === undefined || path.trim() === "") return whole;
    const label = variables.find((v) => v.path === path)?.label;
    return `[${label ?? path}]`;
  });
}
