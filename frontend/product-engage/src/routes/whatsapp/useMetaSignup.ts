import { useCallback, useEffect, useRef, useState } from "react";

import { completeSignup } from "@/api/whatsappSignup";
import type { SignupCompleteIn, SignupCompleteOut, SignupConfig } from "@/api/whatsappSignup";
import { failureMessage } from "@/lib/errors";
import { completeFailureMessage, hasFault, metaFaultMessage } from "./failureMessages";
import { loadMetaSdk, openSignupPopup, parseEmbeddedSignupMessage } from "@/lib/metaSdk";
import type { FbLoginResponse, FbSdk } from "@/lib/metaSdk";

/**
 * Whether the SDK is on the page: `off` when the server has no Meta app configured (nothing is
 * loaded at all), `loading` while the script is in flight, `ready` once `FB.login` can be called
 * from a click, `failed` when the script could not be fetched.
 */
export type SdkState = "off" | "loading" | "ready" | "failed";

/**
 * Where the popup flow is.
 *
 * - `idle`: nothing started (or the page was reloaded — the popup does not survive that).
 * - `waiting`: the popup is open and Meta has not finished.
 * - `completing`: Meta returned a code and `signup/complete` is in flight.
 * - `blocked`: the browser refused to open the popup.
 * - `cancelled`: the window was closed or declined before Meta finished.
 * - `error`: Meta reported an error, or the server refused the code.
 */
export type SignupPhase = "idle" | "waiting" | "completing" | "blocked" | "cancelled" | "error";

/** How long the code waits for Meta's `WA_EMBEDDED_SIGNUP` message before going without it. The
 *  ids in that message are hints (the server discovers the account from the token), and a code
 *  is short-lived, so it is never held for long. */
const SESSION_INFO_GRACE_MS = 1500;

/** How long an empty `FB.login` answer waits for Meta's own `CANCEL`/`ERROR` message before it is
 *  called a closed window. The two arrive in either order, and only the message says why. */
const CANCEL_GRACE_MS = 500;

/** Meta's own id shape: bare ASCII digits, the same pattern the server validates
 *  `SignupCompleteIn.meta_user_id` against (`w1_schemas._DIGITS`). Checked here too so a value
 *  that could never be a real Meta id does not spend the popup's one-time code on a round trip
 *  the server would only 422. */
const META_USER_ID_PATTERN = /^[0-9]{1,30}$/;

interface Session {
  wabaId: string | null;
  phoneNumberId: string | null;
}

export interface MetaSignup {
  sdk: SdkState;
  phase: SignupPhase;
  /** A sentence for `error`. */
  message: string | null;
  /** Call from a click handler: the popup must open inside the user's gesture. */
  start: () => void;
  /** Reload the script after a `failed` load. */
  retryLoad: () => void;
}

/**
 * Meta's Embedded Signup as a hook: preloads the SDK, opens the popup on `start()`, listens for
 * the `WA_EMBEDDED_SIGNUP` message and the `FB.login` callback (in whichever order they arrive),
 * and posts the code to `signup/complete` exactly once.
 *
 * The code is held in a ref for the seconds between Meta returning it and the request leaving —
 * never in state, a URL, storage or a log line.
 */
export function useMetaSignup(
  config: SignupConfig | null,
  onSubmitted: (out: SignupCompleteOut) => void,
): MetaSignup {
  const [sdkState, setSdkState] = useState<SdkState>("off");
  const [phase, setPhase] = useState<SignupPhase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const sdkRef = useRef<FbSdk | null>(null);
  const onSubmittedRef = useRef(onSubmitted);
  onSubmittedRef.current = onSubmitted;

  const codeRef = useRef<string | null>(null);
  const sessionRef = useRef<Session | null>(null);
  // From `FB.login`'s own response, not the `WA_EMBEDDED_SIGNUP` message `sessionRef` holds --
  // a different part of the same round trip. Optional and never verified against Meta; see
  // `SignupCompleteIn.meta_user_id`'s own docstring for why it is captured at all.
  const metaUserIdRef = useRef<string | null>(null);
  const submittedRef = useRef(false);
  const graceTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const stopListening = useRef<(() => void) | null>(null);
  const mounted = useRef(true);

  const enabled = config !== null && config.enabled && config.app_id !== null && config.config_id !== null;
  const appId = enabled ? config.app_id : null;
  const graphVersion = config?.graph_version ?? "";

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimeout(graceTimer.current);
      stopListening.current?.();
    };
  }, []);

  useEffect(() => {
    if (appId === null) {
      setSdkState("off");
      return;
    }
    let active = true;
    setSdkState("loading");
    loadMetaSdk({ appId, graphVersion }).then(
      (sdk) => {
        if (!active) return;
        sdkRef.current = sdk;
        setSdkState("ready");
      },
      () => {
        if (active) setSdkState("failed");
      },
    );
    return () => {
      active = false;
    };
  }, [appId, graphVersion, loadAttempt]);

  const settle = useCallback((next: SignupPhase, text: string | null = null) => {
    clearTimeout(graceTimer.current);
    stopListening.current?.();
    stopListening.current = null;
    if (!mounted.current) return;
    setMessage(text);
    setPhase(next);
  }, []);

  const submit = useCallback(async () => {
    const code = codeRef.current;
    if (code === null || submittedRef.current) return;
    submittedRef.current = true;
    codeRef.current = null;
    clearTimeout(graceTimer.current);
    stopListening.current?.();
    stopListening.current = null;
    if (mounted.current) setPhase("completing");
    const session = sessionRef.current;
    const metaUserId = metaUserIdRef.current;
    const body: SignupCompleteIn = {
      code,
      ...(session?.wabaId != null ? { waba_id: session.wabaId } : {}),
      ...(session?.phoneNumberId != null ? { phone_number_id: session.phoneNumberId } : {}),
      ...(metaUserId != null ? { meta_user_id: metaUserId } : {}),
    };
    try {
      const out = await completeSignup(body);
      // The popup's job is done. Back to idle, so coming back to the Meta step later offers a
      // fresh window instead of sitting on "starting setup" for a setup that has already started.
      if (mounted.current) setPhase("idle");
      onSubmittedRef.current(out);
    } catch (caught) {
      settle("error", completeFailureMessage(caught, failureMessage(caught)));
    }
  }, [settle]);

  const start = useCallback(() => {
    const sdk = sdkRef.current;
    const configId = config?.config_id ?? null;
    if (sdk === null || configId === null) return;

    codeRef.current = null;
    sessionRef.current = null;
    metaUserIdRef.current = null;
    submittedRef.current = false;
    clearTimeout(graceTimer.current);
    stopListening.current?.();
    setMessage(null);

    const onMessage = (event: MessageEvent) => {
      const signup = parseEmbeddedSignupMessage(event);
      if (signup === null) return;
      if (signup.kind === "finish") {
        sessionRef.current = { wabaId: signup.wabaId, phoneNumberId: signup.phoneNumberId };
        if (codeRef.current !== null) void submit();
      } else if (signup.kind === "cancel" && !hasFault(signup)) {
        codeRef.current = null;
        settle("cancelled");
      } else {
        // An error, or a CANCEL that carries Meta's reason: say what Meta said.
        codeRef.current = null;
        settle("error", metaFaultMessage(signup));
      }
    };
    window.addEventListener("message", onMessage);
    stopListening.current = () => window.removeEventListener("message", onMessage);

    const onResponse = (response: FbLoginResponse) => {
      const code = response.authResponse?.code ?? null;
      const userId = response.authResponse?.userID;
      if (userId != null && META_USER_ID_PATTERN.test(userId)) metaUserIdRef.current = userId;
      if (code === null || code === "") {
        // Closed or declined, or Meta stopped it and is about to say why: wait a moment for the
        // message before settling on "closed".
        clearTimeout(graceTimer.current);
        graceTimer.current = setTimeout(() => {
          if (!submittedRef.current) settle("cancelled");
        }, CANCEL_GRACE_MS);
        return;
      }
      codeRef.current = code;
      if (sessionRef.current !== null) {
        void submit();
      } else {
        graceTimer.current = setTimeout(() => void submit(), SESSION_INFO_GRACE_MS);
      }
    };

    setPhase("waiting");
    const { blocked } = openSignupPopup(sdk, configId, onResponse);
    if (blocked) settle("blocked");
  }, [config, settle, submit]);

  const retryLoad = useCallback(() => setLoadAttempt((n) => n + 1), []);

  return { sdk: sdkState, phase, message, start, retryLoad };
}
