import { useCallback, useState } from "react";
import type { ReactNode } from "react";

import { useAuth } from "@opsgrids/client";
import { ConfirmDialog } from "@opsgrids/ui";

import { getPutawayQueue, purgePutawayQueue } from "@/lib/putawayQueue";

/**
 * Signing out of this app: empty the put-away queue, then end the session — every entry, whoever
 * made it, because a shared handset must not keep one person's GPS fixes for the next. Both places
 * that sign out (the shell's account menu and Profile) go through here, so neither can forget.
 * The queue is emptied again when the session has ended, for what was confirmed while it was ending;
 * a pass that is already sending reads the live queue before each send, so the first purge also
 * stops it from sending the rest (`lib/putawaySync.ts`).
 *
 * Put-aways that never reached the server would be lost with the queue, and a stock move silently
 * lost is worse than an extra tap, so with any left the worker is asked first. Nothing is asked
 * when the queue is empty, and a session that merely expires (a 401) is not a sign-out at all:
 * the queue stays for the same person to sync after they sign back in.
 */
export function useSignOut(): { requestSignOut: () => void; signingOut: boolean; dialog: ReactNode } {
  const { logout } = useAuth();
  const [unsynced, setUnsynced] = useState<number | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  const signOutNow = useCallback(() => {
    setSigningOut(true);
    purgePutawayQueue();
    // `logout()` clears the local session even when the server call fails; nothing to recover. It
    // is a slow round trip, though, and the app is usable until it lands: a put-away confirmed in
    // that window is queued after the purge above, so purge again once the session is gone.
    void logout()
      .catch(() => undefined)
      .finally(() => purgePutawayQueue());
  }, [logout]);

  const requestSignOut = useCallback(() => {
    if (signingOut) return;
    const waiting = getPutawayQueue().length;
    if (waiting > 0) setUnsynced(waiting);
    else signOutNow();
  }, [signingOut, signOutNow]);

  const dialog = (
    <ConfirmDialog
      open={unsynced !== null}
      onClose={() => setUnsynced(null)}
      onConfirm={() => {
        setUnsynced(null);
        signOutNow();
      }}
      title="Sign out and discard put-aways?"
      body={
        unsynced === 1
          ? "1 put-away on this phone hasn't been recorded. Signing out discards it, and that stock will not be moved in the system."
          : `${unsynced ?? 0} put-aways on this phone haven't been recorded. Signing out discards them, and that stock will not be moved in the system.`
      }
      confirmLabel="Sign out and discard"
      cancelLabel="Stay signed in"
    />
  );

  return { requestSignOut, signingOut, dialog };
}
