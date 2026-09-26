import { InlineAlert } from "@/ui";
import { formatInstant } from "@/lib/format";

import { historyLabel, historySourceLabel, templateName, VARIABLE_KIND_LABEL } from "./labels";
import type { TemplateView } from "./useTemplateView";
import { WhatsAppBubble } from "./WhatsAppBubble";

/**
 * What is known about one template: how it reads, the variables it takes and how Meta has treated
 * it so far. Shared by the list's side panel and the detail page, so the two can never describe a
 * template differently. `level` is the heading rank of the sections, which is the caller's to set:
 * under the panel's own `h2` they are `h3`, on the detail page under the `h1` they are `h2`.
 */
export function TemplateDetailBody({ view, level }: { view: TemplateView; level: "h2" | "h3" }) {
  const { detail, preview } = view;
  const Heading = level;
  return (
    <div className="flex flex-col gap-6">
      {detail.rejection_reason !== null && detail.rejection_reason !== "" ? (
        <InlineAlert tone="danger" live="off" title="Meta rejected this template">
          {detail.rejection_reason}
        </InlineAlert>
      ) : null}

      <WhatsAppBubble
        text={preview.text}
        buttons={preview.buttons}
        label={templateName(detail)}
      />

      <section aria-label="Variables" className="flex flex-col gap-2">
        <Heading className="text-card-title text-ink">Variables</Heading>
        {detail.variables.length === 0 ? (
          <p className="text-body-sm text-muted">This template has no variables.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {detail.variables.map((variable) => (
              <li
                key={variable.index}
                className="flex items-center gap-3 rounded-tile border border-line px-3 py-2"
              >
                <span
                  aria-hidden
                  className="grid size-6 shrink-0 place-items-center rounded-chip bg-accent-soft text-caption font-bold text-accent-ink tnum"
                >
                  {variable.index}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body-sm font-semibold text-ink">
                    {variable.name}
                  </span>
                  <span className="block truncate text-caption text-muted">
                    {VARIABLE_KIND_LABEL[variable.kind]}
                    {variable.example === "" ? "" : ` · Example: ${variable.example}`}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Approval history" className="flex flex-col gap-2">
        <Heading className="text-card-title text-ink">Approval history</Heading>
        {detail.history.length === 0 ? (
          <p className="text-body-sm text-muted">No status changes recorded yet.</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {detail.history.map((entry, position) => (
              <li
                key={`${position}-${entry.at}`}
                className="flex items-baseline justify-between gap-3 text-body-sm"
              >
                <span className="text-ink">
                  {historyLabel(entry)}
                  <span className="ml-2 text-caption text-muted">{historySourceLabel(entry.source)}</span>
                </span>
                <time dateTime={entry.at} className="shrink-0 text-muted tnum">
                  {formatInstant(entry.at)}
                </time>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
