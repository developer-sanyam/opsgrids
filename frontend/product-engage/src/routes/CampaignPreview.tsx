import { useState } from "react";

import { Switch } from "@opsgrids/ui";

import { framePolicy } from "@/lib/previewPolicy";

/**
 * What a recipient will see, without ever running what the author wrote.
 *
 * **The HTML body is author-supplied markup, so it is never put into this document.** It goes into
 * an `<iframe srcdoc>` with an empty `sandbox` attribute: no scripts, no forms, no popups, no
 * top-level navigation, and an opaque origin, so nothing in it can read this app's session or
 * reach its DOM. A `Content-Security-Policy` meta tag prepended to the markup narrows it further —
 * nothing loads except inline styles. There is no `dangerouslySetInnerHTML` anywhere in this
 * product, and this component is why that stays true.
 *
 * **Remote images are off until the person previewing asks for them.** Every `<img src="https://…">`
 * in a preview is a request from *this browser* to whoever hosts the image, carrying the previewer's
 * IP address and user agent — a tracking pixel in a campaign body would tell its host that someone
 * on this team opened the editor, and when. `img-src 'none'` is the default and the switch changes
 * it to `https:`; it is per preview, never remembered, and there is no way to load a non-`https`
 * or `data:` image at all.
 *
 * The plain-text body and the subject are rendered as React text children, which escape.
 *
 * The service sends both bodies **exactly as written** (`services.campaign_message`): there is no
 * merge-field substitution, so `{{first_name}}` would arrive as those characters. It also appends an
 * unsubscribe link to every message, which is why the note below says so rather than the preview
 * pretending to draw it.
 */

export function CampaignPreview({
  subject,
  html,
  text,
  heading: Heading = "h2",
}: {
  subject: string;
  html: string;
  text: string;
  /** The level of the section's own heading — `h3` where it sits under another section's `h2`,
   *  so the outline never repeats a level. */
  heading?: "h2" | "h3";
}) {
  const [remoteImages, setRemoteImages] = useState(false);
  const hasHtml = html.trim() !== "";
  const hasText = text.trim() !== "";

  return (
    <section aria-labelledby="campaign-preview-heading" className="flex flex-col gap-4">
      <Heading id="campaign-preview-heading" className="text-base font-bold text-ink">
        Preview
      </Heading>

      <div className="flex flex-col gap-1">
        <span className="text-xs font-bold uppercase tracking-wider text-faint">Subject</span>
        <p className="text-sm font-semibold text-ink">
          {subject.trim() === "" ? <span className="font-normal text-muted">No subject yet</span> : subject}
        </p>
      </div>

      {hasHtml ? (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-bold uppercase tracking-wider text-faint">HTML version</span>
          {/* A fixed height, so the preview never shifts the form beneath it as the author types. */}
          <iframe
            title="HTML preview of the campaign body"
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={`${framePolicy(remoteImages)}${html}`}
            className="h-80 w-full rounded-tile border border-line"
          />
          <Switch
            checked={remoteImages}
            onChange={setRemoteImages}
            label="Load remote images"
            description={
              remoteImages
                ? "Images are being fetched from the addresses in the body. Each host can see your IP address and browser."
                : "Images are blocked. Turning this on fetches each one from wherever it is hosted, which tells that host your IP address and browser."
            }
          />
        </div>
      ) : null}

      {hasText ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-bold uppercase tracking-wider text-faint">
            Plain-text version
          </span>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-tile border border-line bg-ground p-3 font-mono text-xs text-ink">
            {text}
          </pre>
        </div>
      ) : null}

      {!hasHtml && !hasText ? (
        <p className="text-sm text-muted">Nothing to preview yet — write a body and it appears here.</p>
      ) : null}

      <p className="text-xs text-muted">
        Sent exactly as written: merge fields such as {"{{first_name}}"} are not filled in. An
        unsubscribe link is added to the foot of every message automatically. Remote images are
        blocked here unless you turn them on above; what a recipient&rsquo;s mail app shows is up to
        that app.
      </p>
    </section>
  );
}
