/**
 * `useStepUp()` — run a dangerous call ("reveal", "unmask") behind a fresh one-time code.
 *
 *     const stepUp = useStepUp();
 *     ...
 *     const phone = await stepUp.run((headers) =>
 *       apiRequest<Unmasked>(path, { method: "POST", body, ...revealRequest(headers) }),
 *     );
 *     ...
 *     <StepUpDialog {...stepUp.dialogProps} />
 *
 * `run` asks for a code when it has no live step-up token, and the dialog it drives (the
 * presentational `StepUpDialog` in `@opsgrids/ui`, fed by `dialogProps`) stays open through a
 * wrong code, a lockout or a dropped connection until the person succeeds or closes it. Closing
 * it rejects `run` with a `StepUpCancelledError` (`isStepUpCancelled`) — catch it and do nothing.
 * `withStepUp` (this folder) is the policy; this hook is the dialog wired to it.
 *
 * The hook holds no token. The token is in `holder.ts`, in memory, shared by every screen, and
 * dropped by this hook when its screen unmounts — navigating away ends the access the code
 * earned. A screen that stays mounted while a drawer or dialog that revealed something closes
 * calls `clear()` itself.
 *
 * Nothing here authorizes anything. The dialog is a prompt; the reveal endpoint checks the token's
 * `amr`, the permission and the scope on every call.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getDashboardOrigin } from "../auth/origin";
import { StepUpError } from "../auth/stepUpError";
import type { StepUpFailure } from "../auth/stepUpError";
import type { StepUpToken } from "../auth/types";
import { StepUpCancelledError } from "./cancelled";
import { clearStepUpToken } from "./holder";
import { requestStepUpToken } from "./requestToken";
import type { RevealHeaders } from "./reveal";
import { withStepUp } from "./withStepUp";
import type { StepUpRunOptions } from "./withStepUp";

/** Whether a code can help at all: `ready` asks for one; the others say why none can. */
export type StepUpMfaState = "ready" | "not-enrolled" | "sso" | "reauthenticate";

/** What the last attempt at the code said, for the dialog to word. */
export type StepUpPromptError = "wrong-code" | "locked" | "unavailable";

/**
 * Where a step-up is right now. `asking`: the dialog is open for a code. `verifying`: a code is
 * with the server. `running`: the token is good and the reveal call is out. `idle`: nothing.
 * Use it to disable the controls that start a reveal, so a second one does not stack on the first.
 */
export type StepUpPhase = "idle" | "asking" | "verifying" | "running";

/**
 * Exactly `StepUpDialog`'s props in `@opsgrids/ui`. `@opsgrids/client` and `@opsgrids/ui` cannot
 * import each other, so the two are twins by structure and the compiler checks the seam where an
 * app spreads one into the other: `<StepUpDialog {...stepUp.dialogProps} />`.
 */
export interface StepUpPromptProps {
  open: boolean;
  busy: boolean;
  error: StepUpPromptError | undefined;
  mfaState: StepUpMfaState;
  onSubmit: (code: string) => void;
  onCancel: () => void;
  /**
   * Where the dashboard's Security page is — where someone with no second factor goes. An absolute
   * URL on the dashboard's host (the configured auth origin, or the production dashboard when the
   * app runs same-origin): a product app has no `/account/security` of its own.
   */
  securityHref: string;
}

export interface UseStepUpResult {
  /**
   * Run `reveal` with a step-up token, asking for a code first when there is no live one. Resolves
   * to what `reveal` returns. Rejects with `StepUpCancelledError` when the person closes the
   * dialog, and otherwise with whatever `reveal` throws. Two `run`s that both need a code share
   * one dialog.
   */
  run: <T>(reveal: (headers: RevealHeaders) => Promise<T>, options?: StepUpRunOptions) => Promise<T>;
  state: StepUpPhase;
  /** Spread onto `<StepUpDialog />`, which the screen renders once. */
  dialogProps: StepUpPromptProps;
  /** Drop the held token now — when the drawer or dialog that revealed something closes. */
  clear: () => void;
}

interface Prompt {
  mfaState: StepUpMfaState;
  error: StepUpPromptError | undefined;
  busy: boolean;
}

const OPENING: Prompt = { mfaState: "ready", error: undefined, busy: false };
const VERIFYING: Prompt = { mfaState: "ready", error: undefined, busy: true };

/** The dashboard route where a member sets up their authenticator app. */
const SECURITY_PATH = "/account/security";

function promptAfter(failure: StepUpFailure): Prompt {
  switch (failure) {
    case "not-enrolled":
      return { mfaState: "not-enrolled", error: undefined, busy: false };
    case "sso":
      return { mfaState: "sso", error: undefined, busy: false };
    case "reauthenticate":
      return { mfaState: "reauthenticate", error: undefined, busy: false };
    default:
      return { mfaState: "ready", error: failure, busy: false };
  }
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  const parts: { resolve?: (value: T) => void; reject?: (reason: unknown) => void } = {};
  const promise = new Promise<T>((resolve, reject) => {
    parts.resolve = resolve;
    parts.reject = reject;
  });
  return {
    promise,
    resolve: (value) => parts.resolve?.(value),
    reject: (reason) => parts.reject?.(reason),
  };
}

export function useStepUp(): UseStepUpResult {
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [inFlight, setInFlight] = useState(0);

  /** The code the open dialog is waiting on. Present exactly while the dialog is open. */
  const waiting = useRef<Deferred<StepUpToken> | null>(null);
  /** A code is with the server. A ref, because state lands a render after the keystroke that
   *  sent it — the sixth digit and an Enter can arrive inside that render. */
  const submitting = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // Navigating away: nobody is left to answer the dialog, and the access the code earned
      // does not follow the person to the next screen.
      waiting.current?.reject(new StepUpCancelledError());
      waiting.current = null;
      clearStepUpToken();
    };
  }, []);

  const askForToken = useCallback((): Promise<StepUpToken> => {
    if (waiting.current !== null) return waiting.current.promise;
    const pending = deferred<StepUpToken>();
    waiting.current = pending;
    setPrompt(OPENING);
    return pending.promise;
  }, []);

  const submit = useCallback(async (code: string): Promise<void> => {
    const pending = waiting.current;
    if (pending === null || submitting.current) return;
    submitting.current = true;
    setPrompt(VERIFYING);
    try {
      const token = await requestStepUpToken(code.trim());
      if (waiting.current !== pending) return;
      waiting.current = null;
      setPrompt(null);
      pending.resolve(token);
    } catch (error) {
      if (waiting.current !== pending) return;
      setPrompt(promptAfter(error instanceof StepUpError ? error.failure : "unavailable"));
    } finally {
      submitting.current = false;
    }
  }, []);

  const cancel = useCallback(() => {
    // The request is out and cannot be recalled; the dialog stays until it answers (or times out).
    if (submitting.current) return;
    const pending = waiting.current;
    waiting.current = null;
    setPrompt(null);
    pending?.reject(new StepUpCancelledError());
  }, []);

  const run = useCallback(
    async <T>(
      reveal: (headers: RevealHeaders) => Promise<T>,
      options?: StepUpRunOptions,
    ): Promise<T> => {
      setInFlight((count) => count + 1);
      try {
        return await withStepUp(reveal, askForToken, options);
      } finally {
        if (mounted.current) setInFlight((count) => count - 1);
      }
    },
    [askForToken],
  );

  const securityHref = `${getDashboardOrigin()}${SECURITY_PATH}`;

  const dialogProps = useMemo<StepUpPromptProps>(
    () => ({
      open: prompt !== null,
      busy: prompt?.busy ?? false,
      error: prompt?.error,
      mfaState: prompt?.mfaState ?? "ready",
      onSubmit: (code) => {
        void submit(code);
      },
      onCancel: cancel,
      securityHref,
    }),
    [prompt, submit, cancel, securityHref],
  );

  const state: StepUpPhase =
    prompt !== null ? (prompt.busy ? "verifying" : "asking") : inFlight > 0 ? "running" : "idle";

  return { run, state, dialogProps, clear: clearStepUpToken };
}
