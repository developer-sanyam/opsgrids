/**
 * Meta's JavaScript SDK and the Embedded Signup popup, isolated from React.
 *
 * The SDK is loaded on demand — on the screen that needs it, never at app start — from Meta's own
 * origin, and only once: a second call while it loads shares the first call's promise, and a call
 * after it loaded finds `window.FB`. It is initialised once per `FB` object, so a stub in a test
 * and the real SDK behave the same.
 *
 * `FB.login` is the one call that must happen synchronously inside a click handler, or the browser
 * treats the popup as unrequested and blocks it. That is why loading is a separate step from
 * logging in: the screen preloads, and the button is enabled only once the SDK is there.
 */

/** What `FB.login`'s callback receives. With `response_type: "code"` the authorization code is
 *  on `authResponse`; a closed or declined window leaves `authResponse` empty. */
export interface FbLoginResponse {
  authResponse?: { code?: string | null } | null;
  status?: string;
}

export interface FbLoginOptions {
  config_id: string;
  response_type: "code";
  override_default_response_type: true;
  extras: { setup: Record<string, never>; featureType: string; sessionInfoVersion: string };
}

export interface FbSdk {
  init: (params: { appId: string; autoLogAppEvents: boolean; xfbml: boolean; version: string }) => void;
  login: (callback: (response: FbLoginResponse) => void, options: FbLoginOptions) => void;
}

declare global {
  interface Window {
    FB?: FbSdk;
    fbAsyncInit?: () => void;
  }
}

export const META_SDK_URL = "https://connect.facebook.net/en_US/sdk.js";

/** The script could not be fetched: an ad blocker, a firewall or the network. */
export class MetaSdkLoadError extends Error {
  constructor() {
    super("Meta's sign-in script could not be loaded.");
    this.name = "MetaSdkLoadError";
  }
}

const initialised = new WeakSet<FbSdk>();
let loading: Promise<FbSdk> | null = null;

export interface MetaSdkParams {
  appId: string;
  graphVersion: string;
}

function initOnce(sdk: FbSdk, { appId, graphVersion }: MetaSdkParams): FbSdk {
  if (!initialised.has(sdk)) {
    sdk.init({ appId, autoLogAppEvents: true, xfbml: false, version: graphVersion });
    initialised.add(sdk);
  }
  return sdk;
}

export function loadMetaSdk(params: MetaSdkParams): Promise<FbSdk> {
  if (window.FB !== undefined) return Promise.resolve(initOnce(window.FB, params));
  if (loading !== null) return loading;

  loading = new Promise<FbSdk>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = META_SDK_URL;
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";
    window.fbAsyncInit = () => {
      loading = null;
      if (window.FB === undefined) {
        reject(new MetaSdkLoadError());
        return;
      }
      resolve(initOnce(window.FB, params));
    };
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(new MetaSdkLoadError());
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** The `FB.login` options for Embedded Signup, per the contract. */
export function signupLoginOptions(configId: string): FbLoginOptions {
  return {
    config_id: configId,
    response_type: "code",
    override_default_response_type: true,
    extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
  };
}

/**
 * Open the popup, and say whether the browser let it open.
 *
 * `FB.login` calls `window.open` synchronously and reports nothing back when the browser refuses,
 * so a blocked popup is otherwise indistinguishable from a slow one. `window.open` is observed for
 * the duration of this one call and restored in `finally`; a `null` return is the browser's own
 * refusal. An SDK that opens no window through `window.open` at all (an in-app browser that
 * redirects instead) is not reported as blocked.
 */
export function openSignupPopup(
  sdk: FbSdk,
  configId: string,
  onResponse: (response: FbLoginResponse) => void,
): { blocked: boolean } {
  const original = window.open;
  let refused = false;
  window.open = (...args: Parameters<typeof window.open>) => {
    const opened = original.apply(window, args);
    if (opened === null) refused = true;
    return opened;
  };
  try {
    sdk.login(onResponse, signupLoginOptions(configId));
  } finally {
    window.open = original;
  }
  return { blocked: refused };
}

/**
 * Why Meta stopped the flow, when it says. Meta reports a failure inside the popup as a `CANCEL`
 * event that carries `error_message`, `error_code` and `session_id` (its "error reports"), so a
 * `cancel` is not always the customer closing the window: check `message`/`code` before saying so.
 * `sessionId` is what Meta support asks for.
 */
export interface EmbeddedSignupFault {
  message: string | null;
  code: string | null;
  sessionId: string | null;
}

/** What Meta's `WA_EMBEDDED_SIGNUP` message says happened inside the popup. */
export type EmbeddedSignupEvent =
  | { kind: "finish"; wabaId: string | null; phoneNumberId: string | null }
  | ({ kind: "cancel"; step: string | null } & EmbeddedSignupFault)
  | ({ kind: "error" } & EmbeddedSignupFault);

function isMetaOrigin(origin: string): boolean {
  try {
    const url = new URL(origin);
    return url.protocol === "https:" && (url.hostname === "facebook.com" || url.hostname.endsWith(".facebook.com"));
  } catch {
    return false;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** An id Meta sends as a string or a number, as a string. */
function idText(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return text(value)?.slice(0, 80) ?? null;
}

/** What Meta reported went wrong, from a message's `data`. A sentence is capped: it is shown to
 *  the customer, and it came from a window this page does not control. */
function faultOf(data: Record<string, unknown> | null): EmbeddedSignupFault {
  const message = text(data?.error_message);
  return {
    message: message === null ? null : message.slice(0, 300),
    code: idText(data?.error_code),
    sessionId: idText(data?.session_id),
  };
}

/**
 * Read one `message` event. Only an event from Meta's own origin, of type `WA_EMBEDDED_SIGNUP`,
 * is believed; anything else — another frame, another extension, a page posting to itself — is
 * `null`. The payload arrives as a JSON string from some SDK versions and as an object from
 * others, and neither is trusted to have the shape documented.
 */
export function parseEmbeddedSignupMessage(event: MessageEvent): EmbeddedSignupEvent | null {
  if (!isMetaOrigin(event.origin)) return null;
  let payload: unknown = event.data;
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload) as unknown;
    } catch {
      return null;
    }
  }
  const message = asRecord(payload);
  if (message === null || message.type !== "WA_EMBEDDED_SIGNUP") return null;
  const data = asRecord(message.data);
  const name = text(message.event);
  // The fault is read before the event is called a plain cancellation: Meta documents
  // `error_message`, `error_code` and `session_id` on CANCEL events.
  if (name === "CANCEL") return { kind: "cancel", step: text(data?.current_step), ...faultOf(data) };
  if (name !== null && name.startsWith("FINISH")) {
    return { kind: "finish", wabaId: text(data?.waba_id), phoneNumberId: text(data?.phone_number_id) };
  }
  if (data !== null && text(data.error_message) !== null) {
    return { kind: "error", ...faultOf(data) };
  }
  return null;
}
