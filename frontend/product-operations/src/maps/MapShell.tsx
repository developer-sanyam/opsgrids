import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { APILoadingStatus, APIProvider, useApiLoadingStatus } from "@vis.gl/react-google-maps";

import type { ReportReason } from "@/lib/clientReport";

declare global {
  interface Window {
    /** Google's own hook: called when the key is refused (wrong key, a referrer the key is not
     *  restricted to, an API not enabled, billing off, quota spent). */
    gm_authFailure?: () => void;
  }
}

interface MapShellProps {
  apiKey: string;
  /** Called at most once, with a fixed reason code — never the loader's error, which can carry the
   *  script URL and so the key. The caller swaps in the non-map equivalent. */
  onFailure: (reason: Extract<ReportReason, "script_error" | "auth_failure">) => void;
  children: ReactNode;
}

/**
 * Everything that talks to Google Maps sits inside this one provider, which is also the one place
 * a failure is noticed, in the two ways Google has of failing:
 *
 * - the script does not load (blocked, offline, a bad network): the provider's `onError`, or its
 *   `FAILED` status;
 * - the script loads and then Google refuses the key. That is **not** an error and not a status the
 *   provider reports — Google calls `window.gm_authFailure`, and nothing else says so. This is the
 *   common production failure (a referrer the key does not allow, the Maps JavaScript API or Places
 *   not enabled, billing off), so the hook is registered here, before the script can load.
 *
 * Imported only from lazily loaded modules, so the Maps loader is never in the main bundle.
 */
export function MapShell({ apiKey, onFailure, children }: MapShellProps) {
  const told = useRef(false);
  const onFailureRef = useRef(onFailure);
  onFailureRef.current = onFailure;

  function tell(reason: Extract<ReportReason, "script_error" | "auth_failure">) {
    if (told.current) return;
    told.current = true;
    onFailureRef.current(reason);
  }

  useEffect(() => {
    const previous = window.gm_authFailure;
    window.gm_authFailure = () => {
      previous?.();
      tell("auth_failure");
    };
    return () => {
      if (previous === undefined) delete window.gm_authFailure;
      else window.gm_authFailure = previous;
    };
  }, []);

  return (
    <APIProvider apiKey={apiKey} onError={() => tell("script_error")}>
      <FailureWatcher tell={tell} />
      {children}
    </APIProvider>
  );
}

function FailureWatcher({ tell }: { tell: (reason: "script_error" | "auth_failure") => void }) {
  const status = useApiLoadingStatus();
  useEffect(() => {
    if (status === APILoadingStatus.AUTH_FAILURE) tell("auth_failure");
    else if (status === APILoadingStatus.FAILED) tell("script_error");
  }, [status, tell]);
  return null;
}
