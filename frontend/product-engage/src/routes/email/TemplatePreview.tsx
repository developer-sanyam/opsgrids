import { useMemo } from "react";

import { previewDocument } from "./templateText";

/**
 * The live preview: the subject and body as a recipient's client would show them as text, inside an
 * iframe with an empty `sandbox` attribute — no scripts, no forms, no popups, and an opaque origin,
 * so nothing typed in the editor can reach this page or the session. `previewDocument` escapes the
 * text and adds a content security policy as well; see it for why the frame is not the only guard.
 */
export function TemplatePreview({ subject, body }: { subject: string; body: string }) {
  const html = useMemo(() => previewDocument(subject, body), [subject, body]);
  return (
    <section aria-labelledby="template-preview" className="flex flex-col gap-2">
      <h2 id="template-preview" className="text-sm font-bold text-ink">
        Preview
      </h2>
      <iframe
        title="Email preview"
        sandbox=""
        srcDoc={html}
        className="h-96 w-full rounded-tile border border-line bg-surface"
      />
    </section>
  );
}
