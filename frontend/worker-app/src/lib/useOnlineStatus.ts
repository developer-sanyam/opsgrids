import { useEffect, useState } from "react";

/** `navigator.onLine`, live. A phone in a warehouse basement loses signal mid-shift — this is
 *  what the scan screen and its offline queue (`lib/scanQueue.ts`) watch to know when to try
 *  replaying. It is a hint, not a guarantee (a browser can report "online" on a captive portal
 *  with no real route out), which is why the queue's own retry never assumes a request landed
 *  just because this flipped to `true` — it still waits for the request's own result. */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));

  useEffect(() => {
    function goOnline() {
      setOnline(true);
    }
    function goOffline() {
      setOnline(false);
    }
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  return online;
}
