/**
 * One sealed value (a lead's phone, a contact's number) that stays masked until the person taps
 * Reveal, then shows itself in place and stays visible for the session.
 *
 *     const phone = useRevealedField({ resetKey: lead.id, subject: "lead", noun: "phone number",
 *       load: () => revealLeadField(lead.id, "phone") });
 *
 * **No step-up.** A caller who holds the reveal permission needs no one-time code (owner decision
 * 2026-10-15, ADR-0023 addendum): the server checks the permission, writes an audit row for the
 * call and spends the reveal budget. This hook only asks, once, when the person taps.
 *
 * **Sticky for the session.** The value is kept in `./revealedValues`, in memory and nowhere else,
 * keyed by subject, record and field. Navigating away and back shows it with no call and so no
 * second audit row; sign-out, a new sign-in and a 401 empty it. There is no auto-hide.
 *
 * Every refusal is worded by `./revealMessages` and left in `notice`, for the field to show under
 * itself, where a screen reader hears it.
 *
 * `RevealableField` in `@opsgrids/ui` renders the result; it takes it as a prop because ui may
 * not import this package.
 */

import { useEffect, useRef, useState } from "react";

import { nothingOnFile, revealFailure } from "./revealMessages";
import type { RevealNotice, RevealSubject } from "./revealMessages";
import { getRevealedValue, setRevealedValue } from "./revealedValues";

export interface RevealedFieldOptions {
  /** What the value belongs to: the lead's or the contact's id. */
  resetKey: string;
  subject: RevealSubject;
  /** What is being revealed, as a phrase in a sentence: "phone number", "email address". */
  noun: string;
  /** The reveal call. Resolves `null` when nothing is sealed. */
  load: () => Promise<string | null>;
}

export interface RevealedField {
  /** The real value once revealed in this session; `null` before. */
  value: string | null;
  /** What to say under the field when the reveal produced no value. */
  notice: RevealNotice | null;
  /** The reveal call is out. */
  pending: boolean;
  reveal: () => void;
}

interface Attempt {
  /** The `resetKey` this state was made for. */
  key: string;
  notice: RevealNotice | null;
  pending: boolean;
}

export function useRevealedField({ resetKey, subject, noun, load }: RevealedFieldOptions): RevealedField {
  const cacheKey = `${subject}:${resetKey}:${noun}`;
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  /** Bumped whenever an answer that is still on its way stops being wanted. */
  const epoch = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    return () => {
      epoch.current += 1;
      inFlight.current = false;
      setAttempt(null);
    };
  }, [resetKey]);

  async function run() {
    if (inFlight.current) return;
    inFlight.current = true;
    const ticket = epoch.current;
    const key = resetKey;
    setAttempt({ key, notice: null, pending: true });
    try {
      const value = await load();
      if (epoch.current !== ticket) return;
      if (value === null) {
        setAttempt({ key, notice: nothingOnFile(noun), pending: false });
        return;
      }
      setRevealedValue(cacheKey, value);
      setAttempt({ key, notice: null, pending: false });
    } catch (error) {
      if (epoch.current !== ticket) return;
      setAttempt({ key, notice: revealFailure(error, subject), pending: false });
    } finally {
      if (epoch.current === ticket) inFlight.current = false;
    }
  }

  const current = attempt !== null && attempt.key === resetKey ? attempt : null;

  return {
    value: getRevealedValue(cacheKey),
    notice: current?.notice ?? null,
    pending: current?.pending ?? false,
    reveal: () => {
      void run();
    },
  };
}
