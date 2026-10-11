import { useEffect, useRef } from "react";

import { EyeIcon, EyeOffIcon, MailIcon, PhoneIcon } from "../icons";
import { cn } from "../lib/cn";
import { Button } from "./Button";

/**
 * How long a revealed value stays on screen, in seconds — the wording here. `@opsgrids/client`
 * exports the same number as `DEFAULT_REVEAL_VISIBLE_SECONDS` for the hook's timer.
 */
export const REVEAL_VISIBLE_SECONDS = 30;

/**
 * The state of one reveal, as `useRevealedField` in `@opsgrids/client` returns it. Structural:
 * this package may not import that one, so the field takes the hook's result as a prop.
 */
export interface RevealedFieldView {
  value: string | null;
  notice: { tone: "neutral" | "error"; text: string } | null;
  pending: boolean;
  justHidden: boolean;
  reveal: () => void;
  hide: () => void;
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
  /** Holds the reveal permission (and can step up). Presentation only: the server decides. */
  canReveal: boolean;
  field: RevealedFieldView;
  /** No one-time code is being asked for: a second reveal waits until it is. */
  stepUpIdle: boolean;
}

/** A number reduced to what a dialler takes: digits, and the `+` of a country code. */
function telHref(value: string): string {
  return `tel:${value.replace(/[^\d+]/g, "")}`;
}

/**
 * A masked value with a Reveal that shows the real one in place for `REVEAL_VISIBLE_SECONDS`, or
 * until Hide. Presentational: `useRevealedField` (`@opsgrids/client`) owns the value, the timer
 * and the step-up; the screen renders its one `<StepUpDialog />`.
 *
 * **One button, two labels.** Reveal turns into Hide on the same element, so focus is never on a
 * control that vanishes; the name says which field ("Reveal phone number"), because two of these
 * side by side and "Reveal" twice is not a choice a screen-reader user can make.
 *
 * **Focus finds its way back.** The button is disabled while a code is out, so the dialog cannot
 * return focus to it; when the reveal settles and focus has fallen to the page, it goes back on
 * the button. If the person has moved on, it is left alone.
 *
 * **One live region, under the row,** mounted before anything is said into it: the refusal, that
 * a value is showing and will hide, and that it has hidden. Never a toast.
 */
export function RevealableField({ kind, label, noun, masked, canReveal, field, stepUpIdle }: RevealableFieldProps) {
  const named = kind === "text" ? { noun: noun ?? label ?? "value", label: label ?? noun ?? "Value", Icon: null } : KINDS[kind];
  const buttonRef = useRef<HTMLButtonElement>(null);
  const revealing = useRef(false);

  const { pending } = field;
  useEffect(() => {
    if (pending) {
      revealing.current = true;
      return;
    }
    if (!revealing.current || !stepUpIdle) return;
    revealing.current = false;
    if (document.activeElement === document.body) buttonRef.current?.focus();
  }, [pending, stepUpIdle]);

  const present = masked !== null && masked.trim() !== "";
  const offered = canReveal && present;
  const revealed = field.value !== null;
  const action = revealed ? "Hide" : "Reveal";
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
              href={kind === "phone" ? telHref(field.value) : `mailto:${field.value}`}
              className={cn("min-w-0 break-words transition-colors hover:text-accent", digits)}
            >
              {field.value}
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
        {offered ? (
          <Button
            ref={buttonRef}
            size="sm"
            variant="ghost"
            className="min-w-24"
            leadingIcon={revealed ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
            loading={field.pending}
            disabled={!revealed && !stepUpIdle}
            aria-label={`${action} ${named.noun}`}
            onClick={revealed ? field.hide : field.reveal}
          >
            {action}
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
          {revealed ? (
            <p className="mt-1 text-xs text-muted">
              <span className="sr-only">{named.label} revealed. </span>
              Hides again in {REVEAL_VISIBLE_SECONDS} seconds.
            </p>
          ) : null}
          {field.justHidden ? <p className="sr-only">{named.label} hidden.</p> : null}
        </div>
      ) : null}
    </div>
  );
}
