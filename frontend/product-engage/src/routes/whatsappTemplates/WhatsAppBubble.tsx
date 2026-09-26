import type { TemplateButton, TemplateHeaderType } from "@/api/whatsappTemplates";

import { BUTTON_TYPE_LABEL } from "./labels";

export type BubbleHeader =
  | { kind: "text"; text: string }
  | { kind: "media"; type: Exclude<TemplateHeaderType, "text"> };

const MEDIA_LABEL: Record<Exclude<TemplateHeaderType, "text">, string> = {
  image: "Image header",
  video: "Video header",
  document: "Document header",
};

interface WhatsAppBubbleProps {
  text: string;
  header?: BubbleHeader | undefined;
  footer?: string | undefined;
  buttons?: readonly TemplateButton[] | undefined;
  /** What the preview is of, for the screen reader: "order_shipped". */
  label?: string | undefined;
}

/**
 * A template as the customer will see it: one incoming message in a chat, with its header, text,
 * footer and buttons. It draws exactly what it is given and computes nothing, so the same bubble
 * shows the server's rendered preview and the create flow's client-side one.
 *
 * The buttons are a list, not controls: they are part of the picture, and a preview that could be
 * pressed would suggest it sends something. A blank text is drawn as a quiet placeholder so the
 * panel never collapses while a draft is still empty — no layout shift as the user starts typing.
 */
export function WhatsAppBubble({ text, header, footer, buttons = [], label }: WhatsAppBubbleProps) {
  return (
    <figure
      aria-label={label === undefined ? "Message preview" : `Preview of ${label}`}
      className="flex min-h-32 flex-col gap-2 rounded-card bg-sunken p-4"
    >
      <div className="flex max-w-xs flex-col gap-2 rounded-tile bg-surface p-3 shadow-card">
        {header?.kind === "text" ? (
          <p className="text-body font-bold text-ink break-words">{header.text}</p>
        ) : null}
        {header?.kind === "media" ? (
          <p className="grid h-24 place-items-center rounded-tile bg-sunken text-caption font-semibold text-muted">
            {MEDIA_LABEL[header.type]}
          </p>
        ) : null}
        {text.trim() === "" ? (
          <p className="text-body text-muted">The message will appear here.</p>
        ) : (
          <p className="whitespace-pre-wrap text-body text-ink break-words">{text}</p>
        )}
        {footer !== undefined && footer !== "" ? (
          <p className="text-caption text-muted break-words">{footer}</p>
        ) : null}
      </div>
      {buttons.length > 0 ? (
        <ul className="flex max-w-xs flex-col gap-1">
          {buttons.map((button, position) => (
            <li
              key={`${position}-${button.text}`}
              className="flex flex-col items-center rounded-tile bg-surface px-3 py-2 text-center shadow-card"
            >
              <span className="text-body font-semibold text-accent-ink break-words">
                {button.text}
              </span>
              <span className="text-caption text-muted">{BUTTON_TYPE_LABEL[button.type]}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </figure>
  );
}
