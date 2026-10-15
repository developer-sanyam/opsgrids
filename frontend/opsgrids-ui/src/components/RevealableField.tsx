import { useEffect, useRef } from "react";

import { EyeIcon, MailIcon, MessageIcon, PhoneIcon } from "../icons";
import { cn } from "../lib/cn";
import { formatPhone } from "../lib/phone";
import { Button } from "./Button";

/**
 * The state of one reveal, as `useRevealedField` in `@opsgrids/client` returns it. Structural:
 * this package may not import that one, so the field takes the hook's result as a prop.
 */
export interface RevealedFieldView {
  value: string | null;
  notice: { tone: "neutral" | "error"; text: string } | null;
  pending: boolean;
  reveal: () => void;
}

const KINDS = {
  phone: { noun: "phone number", label: "Phone number", Icon: PhoneIcon },
  email: { noun: "email address", label: "Email address", Icon: MailIcon },
} as const;

interface RevealableFieldProps {
  /** `phone` and `email` link a revealed value (`tel:`, `mailto:`); `text` shows it as text. */
  kind: "phone" | "email" | "text";
  /** The field's name, read before the value: "PAN". Required for `text`; phone and email have their own. */
  label?: string | undefined;
  /** The field in a sentence: "Reveal PAN". Required for `text`. */
  noun?: string | undefined;
  /** The masked form the record carries. `null` or blank: nothing is sealed, so nothing to reveal. */
  masked: string | null;
  /** Holds the reveal permission. Presentation only: the server decides. */
  canReveal: boolean;
  field: RevealedFieldView;
}

/** A number reduced to what a dialler takes: digits, and the `+` of a country code. */
function telHref(value: string): string {
  return `tel:${value.replace(/[^\d+]/g, "")}`;
}

/** A click-to-chat link: `https://wa.me/` takes the international number as digits only, no `+`.
 *  `null` for anything too short to be a full international number. */
function whatsAppHref(value: string): string | null {
  const digits = value.replace(/\D/g, "");
  return digits.length >= 8 && digits.length <= 15 ? `https://wa.me/${digits}` : null;
}

/**
 * A masked value with a Reveal that shows the real one in place, where it stays for the session.
 * Presentational: `useRevealedField` (`@opsgrids/client`) owns the value and the call.
 *
 * **One tap, no code, no auto-hide** (owner decision 2026-10-15, ADR-0023 addendum): a caller
 * who holds the permission taps Reveal and the value appears and stays, as a `tel:` / `mailto:`
 * link with a 44px target so a rep on a call can tap it to dial. The server records every reveal.
 * A revealed phone also gets a WhatsApp action (`https://wa.me/<digits>`, new tab), built from the
 * same revealed value: it never appears while the number is masked and asks for nothing new.
 *
 * **Focus finds its way back.** The Reveal button goes away once the value shows, so when the
 * reveal settles and focus has fallen to the page, it moves to the value's link. If the person
 * has moved on, it is left alone.
 *
 * **One live region, under the row,** mounted before anything is said into it: the refusal, and
 * that a value is showing. Never a toast.
 */
export function RevealableField({ kind, label, noun, masked, canReveal, field }: RevealableFieldProps) {
  const named = kind === "text" ? { noun: noun ?? label ?? "value", label: label ?? noun ?? "Value", Icon: null } : KINDS[kind];
  const buttonRef = useRef<HTMLButtonElement>(null);
  const linkRef = useRef<HTMLAnchorElement>(null);
  const revealing = useRef(false);

  const { pending } = field;
  const revealedNow = field.value !== null;
  useEffect(() => {
    if (pending) {
      revealing.current = true;
      return;
    }
    if (!revealing.current) return;
    revealing.current = false;
    if (document.activeElement === document.body) (linkRef.current ?? buttonRef.current)?.focus();
  }, [pending, revealedNow]);

  const present = masked !== null && masked.trim() !== "";
  const offered = canReveal && present;
  const revealed = field.value !== null;
  const Icon = named.Icon;
  const digits = kind === "phone" && "tnum";

  return (
    <div className="flex flex-col">
      <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1", offered && "min-h-9")}>
        <span className="inline-flex min-w-0 items-center gap-2 text-ink-soft">
          {Icon !== null ? <Icon className="size-4 shrink-0 text-faint" /> : null}
          <span className="sr-only">{named.label}: </span>
          {field.value !== null && kind !== "text" ? (
            <a
              ref={linkRef}
              href={kind === "phone" ? telHref(field.value) : `mailto:${field.value}`}
              className={cn(
                "inline-flex min-h-11 min-w-0 items-center break-words transition-colors hover:text-accent",
                digits,
              )}
            >
              {kind === "phone" ? formatPhone(field.value) : field.value}
            </a>
          ) : field.value !== null ? (
            <span className="min-w-0 break-words tnum">{field.value}</span>
          ) : present ? (
            <span className={cn("min-w-0 break-words", digits)}>{masked}</span>
          ) : (
            <>
              <span aria-hidden="true">—</span>
              <span className="sr-only">None on file</span>
            </>
          )}
        </span>
        {kind === "phone" && field.value !== null && whatsAppHref(field.value) !== null ? (
          <a
            href={whatsAppHref(field.value) ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`WhatsApp ${formatPhone(field.value)} (opens in a new tab)`}
            className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-tile px-2 text-sm font-semibold text-muted transition-colors hover:text-accent"
          >
            <MessageIcon className="size-4" aria-hidden="true" />
            WhatsApp
          </a>
        ) : null}
        {offered && !revealed ? (
          <Button
            ref={buttonRef}
            size="sm"
            variant="ghost"
            className="min-w-24"
            leadingIcon={<EyeIcon className="size-4" />}
            loading={field.pending}
            aria-label={`Reveal ${named.noun}`}
            onClick={field.reveal}
          >
            Reveal
          </Button>
        ) : null}
      </div>
      {canReveal ? (
        <div aria-live="polite">
          {field.notice !== null ? (
            <p className={cn("mt-1 text-xs", field.notice.tone === "error" ? "text-negative" : "text-muted")}>
              {field.notice.text}
            </p>
          ) : null}
          {revealed ? <p className="sr-only">{named.label} revealed.</p> : null}
        </div>
      ) : null}
    </div>
  );
}
