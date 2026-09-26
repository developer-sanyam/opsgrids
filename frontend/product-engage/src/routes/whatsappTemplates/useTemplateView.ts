import { useCallback } from "react";

import { useAsync } from "@opsgrids/client";

import { getTemplate, previewTemplate } from "@/api/whatsappTemplates";
import type { TemplatePreview, WhatsAppTemplateDetail } from "@/api/whatsappTemplates";

import { substitute } from "./draft";

export interface TemplateView {
  detail: WhatsAppTemplateDetail;
  preview: TemplatePreview;
}

/**
 * One template, fully read: the row with its history, and the message rendered with the examples
 * Meta reviewed.
 *
 * The render is asked of the service (`.../preview`) because the service is the authority on how a
 * template's variables are filled. If only that call fails, the message is still drawn — from the
 * row's own `body_preview` with the same `{{n}}` substitution — rather than failing the whole
 * panel over a picture; a failure of the row itself is a real error and is surfaced.
 */
export function useTemplateView(id: string) {
  const loader = useCallback(async (): Promise<TemplateView> => {
    const detail = await getTemplate(id);
    const values: Record<string, string> = {};
    for (const variable of detail.variables) values[String(variable.index)] = variable.example;
    const preview = await previewTemplate(id, values).catch(
      (): TemplatePreview => ({
        text: substitute(detail.body_preview, values),
        buttons: [],
      }),
    );
    return { detail, preview };
  }, [id]);
  return useAsync(loader, [id]);
}
