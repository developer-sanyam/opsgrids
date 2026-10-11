/**
 * One sealed value (a lead's phone, a contact's number) that stays masked until someone asks for
 * it behind a one-time code, shows itself in place, and masks itself again.
 *
 *     const stepUp = useStepUp();                        // once per screen, with <StepUpDialog />
 *     const phone = useRevealedField({ resetKey: lead.id, stepUp, subject: "lead", noun: "phone number",
 *       load: (headers) => revealLeadField(lead.id, "phone", headers) });
 *
 * **The value lives in this hook's state and nowhere else.** Not in a store, not in storage, not in
 * the URL, not in the record the screen loaded. It is dropped by any of: `hide()`, the timer
 * (`REVEAL_VISIBLE_SECONDS`), `resetKey` changing (another lead, another contact), and the
 * component unmounting. While `resetKey` and the state disagree the value is not returned at all,
 * so there is no frame in which one record's number is painted on another's screen.
 *
 * **One code serves a screen.** The hook runs `load` through the screen's `useStepUp().run`, which
 * keeps the step-up token for its lifetime: a phone and then an email is one prompt. When the
 * value is dropped by `resetKey` or unmount, the token goes with it — leaving a record ends the
 * access the code earned. The screen renders the one `<StepUpDialog />`; this hook only asks.
 *
 * A closed dialog is not an error and says nothing. Every refusal is worded by `./revealMessages`
 * and left in `notice`, for the field to show under itself, where a screen reader hears it.
 *
 * `RevealableField` in `@opsgrids/ui` renders the result; it takes it as a prop because ui may
 * not import this package. An app with an offline fixtures mode passes a `stepUp` whose `run`
 * calls `load` directly while that mode is on.
 */

import { useEffect, useRef, useState } from "react";

import { isStepUpCancelled } from "./cancelled";
import type { RevealHeaders } from "./reveal";
import { needsFreshCode, nothingOnFile, revealFailure } from "./revealMessages";
import type { RevealNotice, RevealSubject } from "./revealMessages";
import type { UseStepUpResult } from "./useStepUp";

/**
 * How long a revealed value stays on screen before it masks itself again. `@opsgrids/ui` exports
 * the same number as `REVEAL_VISIBLE_SECONDS` for the field's wording; a CRM test keeps them equal.
 */
export const DEFAULT_REVEAL_VISIBLE_SECONDS = 30;

export interface RevealedFieldOptions {
  /**
   * What the value belongs to: the lead's or the contact's id. A different key hides the value,
   * drops the token the code earned and abandons a reveal that is still on its way.
   */
  resetKey: string;
  /** The screen's one `useStepUp()`. */
  stepUp: UseStepUpResult;
  subject: RevealSubject;
  /** What is being revealed, as a phrase in a sentence: "phone number", "email address". */
  noun: string;
  /** The reveal call. Resolves `null` when nothing is sealed. Receives the step-up bearer. */
  load: (headers: RevealHeaders) => Promise<string | null>;
  /** How long the value shows before it hides again. Default `DEFAULT_REVEAL_VISIBLE_SECONDS`. */
  visibleSeconds?: number | undefined;
}

export interface RevealedField {
  /** The real value while it is showing; `null` the rest of the time. */
  value: string | null;
  /** What to say under the field when the reveal produced no value. */
  notice: RevealNotice | null;
  /** The reveal is under way: the dialog is open, a code is being checked, or the call is out. */
  pending: boolean;
  /** The value was showing and has just been hidden again — by Hide or by the timer. */
  justHidden: boolean;
  reveal: () => void;
  hide: () => void;
}

interface Shown {
  /** The `resetKey` this state was made for. */
  key: string;
  value: string | null;
  notice: RevealNotice | null;
  pending: boolean;
  justHidden: boolean;
}

export function useRevealedField({
  resetKey,
  stepUp,
  subject,
  noun,
  load,
  visibleSeconds = DEFAULT_REVEAL_VISIBLE_SECONDS,
}: RevealedFieldOptions): RevealedField {
  const [shown, setShown] = useState<Shown | null>(null);
  /** Bumped whenever an answer that is still on its way stops being wanted. */
  const epoch = useRef(0);
  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** A code may have been entered for this key, so a token may be held. */
  const asked = useRef(false);
  const { clear } = stepUp;

  useEffect(() => {
    return () => {
      epoch.current += 1;
      inFlight.current = false;
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      setShown(null);
      if (asked.current) {
        asked.current = false;
        clear();
      }
    };
  }, [resetKey, clear]);

  function hide() {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    setShown((previous) =>
      previous === null
        ? null
        : { key: previous.key, value: null, notice: null, pending: false, justHidden: previous.value !== null },
    );
  }

  async function run() {
    if (inFlight.current) return;
    inFlight.current = true;
    asked.current = true;
    const ticket = epoch.current;
    const key = resetKey;
    setShown({ key, value: null, notice: null, pending: true, justHidden: false });
    try {
      const value = await stepUp.run(load);
      if (epoch.current !== ticket) return;
      if (value === null) {
        setShown({ key, value: null, notice: nothingOnFile(noun), pending: false, justHidden: false });
        return;
      }
      setShown({ key, value, notice: null, pending: false, justHidden: false });
      timer.current = setTimeout(hide, visibleSeconds * 1000);
    } catch (error) {
      if (epoch.current !== ticket) return;
      if (isStepUpCancelled(error)) {
        setShown(null);
        return;
      }
      if (needsFreshCode(error)) clear();
      setShown({ key, value: null, notice: revealFailure(error, subject), pending: false, justHidden: false });
    } finally {
      if (epoch.current === ticket) inFlight.current = false;
    }
  }

  const current = shown !== null && shown.key === resetKey ? shown : null;

  return {
    value: current?.value ?? null,
    notice: current?.notice ?? null,
    pending: current?.pending ?? false,
    justHidden: current?.justHidden ?? false,
    reveal: () => {
      void run();
    },
    hide,
  };
}
