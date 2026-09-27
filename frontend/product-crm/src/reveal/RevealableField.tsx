import { useEffect, useRef } from "react";

import type { RevealHeaders, UseStepUpResult } from "@opsgrids/client";
import { cn, EyeIcon, EyeOffIcon, MailIcon, PhoneIcon } from "@opsgrids/ui";

import { Button } from "@/ui";
import type { RevealSubject } from "./messages";
import { REVEAL_VISIBLE_SECONDS, useRevealedField } from "./useRevealedField";

const KINDS = {
  phone: { noun: "phone number", label: "Phone number", Icon: PhoneIcon },
  email: { noun: "email address", label: "Email address", Icon: MailIcon },
} as const;

export type RevealableKind = keyof typeof KINDS;

/** A number reduced to what a dialler takes: digits, and the `+` of a country code. */
function telHref(value: string): string {
  return `tel:${value.replace(/[^\d+]/g, "")}`;
}

interface RevealableFieldProps {
  kind: RevealableKind;
  /** The masked form the record carries. `null` or blank: nothing is sealed, so nothing to reveal. */
  masked: string | null;
  subject: RevealSubject;
  /**
   * Whether the caller holds the reveal permission. Presentation only: without it the field is the
   * masked line and nothing more, and the server refuses the call whatever this says.
   */
  canReveal: boolean;
  /** The screen's one `useStepUp()`; the screen renders its `<StepUpDialog />`. */
  stepUp: UseStepUpResult;
  /** The record's id. Another record hides the value and drops the code's access. */
  resetKey: string;
  load: (headers: RevealHeaders) => Promise<string | null>;
}

/**
 * A masked phone number or email address, with a Reveal that shows the real one in place — as a
 * `tel:` / `mailto:` link — for `REVEAL_VISIBLE_SECONDS`, or until Hide.
 *
 * **One button, two labels.** Reveal turns into Hide on the same element, so focus is never on a
 * control that vanishes; the name says which field ("Reveal phone number") because two of these
 * sit side by side and "Reveal" twice is not a choice a screen-reader user can make.
 *
 * **Focus finds its way back.** The step-up dialog returns focus to the control that opened it, but
 * that control is disabled while a code is out (so no second reveal can start), and a disabled
 * button cannot take focus. When the reveal settles, if focus has fallen to the page, it is put
 * back on the button; if the person has moved on, it is left alone.
 *
 * **Everything that changes without a click is in the live region** under the row: the refusal
 * (worded in `./messages`), that a value is showing and will hide, and that it has hidden. The
 * region is there before any of it is, which is what makes a screen reader say it. It is never a
 * toast, which would go away and leave the person unsure whether anything was shown.
 *
 * This is a candidate for `@opsgrids/ui` once a second product needs it (ADR-0001); it lives here
 * because the step-up call it wraps is the only part that is CRM's.
 */
export function RevealableField({
  kind,
  masked,
  subject,
  canReveal,
  stepUp,
  resetKey,
  load,
}: RevealableFieldProps) {
  const { noun, label, Icon } = KINDS[kind];
  const field = useRevealedField({ resetKey, stepUp, subject, noun, load });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const revealing = useRef(false);

  const { pending } = field;
  const stepUpIdle = stepUp.state === "idle";
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

  return (
    <div className="flex flex-col">
      <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1", offered && "min-h-9")}>
        <span className="inline-flex min-w-0 items-center gap-2 text-ink-soft">
          <Icon className="size-4 shrink-0 text-faint" />
          <span className="sr-only">{label}: </span>
          {field.value !== null ? (
            <a
              href={kind === "phone" ? telHref(field.value) : `mailto:${field.value}`}
              className={cn(
                "min-w-0 break-words transition-colors hover:text-accent",
                kind === "phone" && "tnum",
              )}
            >
              {field.value}
            </a>
          ) : present ? (
            <span className={cn("min-w-0 break-words", kind === "phone" && "tnum")}>{masked}</span>
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
            aria-label={`${action} ${noun}`}
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
              <span className="sr-only">{label} revealed. </span>
              Hides again in {REVEAL_VISIBLE_SECONDS} seconds.
            </p>
          ) : null}
          {field.justHidden ? <p className="sr-only">{label} hidden.</p> : null}
        </div>
      ) : null}
    </div>
  );
}
