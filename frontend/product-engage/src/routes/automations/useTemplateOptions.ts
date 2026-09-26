import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@opsgrids/client";

import { listEmailTemplateOptions, listWhatsAppTemplateOptions } from "@/api/automations";
import type { MessageChannel, TemplateOption } from "@/api/automations";
import type { TemplateCatalogue } from "./graph";

/**
 * The templates a send step can point at, one list per channel, read only once a send step exists.
 * Each channel settles on its own: WhatsApp templates can fail to load while email's arrive, and
 * the step on the channel that failed says so and offers a retry, instead of the whole builder
 * failing for a list that only one step needs.
 */

export type SourceState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; items: TemplateOption[] }
  | { status: "error"; message: string };

const LOADERS: Record<MessageChannel, () => Promise<TemplateOption[]>> = {
  whatsapp: () => listWhatsAppTemplateOptions(),
  email: () => listEmailTemplateOptions(),
};

export interface TemplateSources {
  sources: Record<MessageChannel, SourceState>;
  /** Only the channels that loaded. */
  catalogue: TemplateCatalogue;
  reload: (channel: MessageChannel) => void;
}

/** `wanted`: the channels some step in the flow sends on. */
export function useTemplateOptions(wanted: MessageChannel[]): TemplateSources {
  const [sources, setSources] = useState<Record<MessageChannel, SourceState>>({
    whatsapp: { status: "idle" },
    email: { status: "idle" },
  });

  const load = useCallback((channel: MessageChannel) => {
    setSources((current) => ({ ...current, [channel]: { status: "loading" } }));
    LOADERS[channel]().then(
      (items) => setSources((current) => ({ ...current, [channel]: { status: "ready", items } })),
      (caught: unknown) =>
        setSources((current) => ({
          ...current,
          [channel]: { status: "error", message: caught instanceof ApiError ? caught.message : "Couldn't reach the server." },
        })),
    );
  }, []);

  const key = [...new Set(wanted)].sort().join(",");
  useEffect(() => {
    for (const channel of key === "" ? [] : (key.split(",") as MessageChannel[])) {
      if (sources[channel].status === "idle") load(channel);
    }
  }, [key, sources, load]);

  const catalogue = useMemo<TemplateCatalogue>(() => {
    const out: TemplateCatalogue = {};
    if (sources.whatsapp.status === "ready") out.whatsapp = sources.whatsapp.items;
    if (sources.email.status === "ready") out.email = sources.email.items;
    return out;
  }, [sources]);

  return { sources, catalogue, reload: load };
}
