import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { getOwners, listCompanies, listContacts } from "@/api/client";
import { DirectoryContext, EMPTY_DIRECTORY } from "./directoryContext";
import type { Directory } from "./directoryContext";

/** A page big enough to hold the whole (small) book in one request — the same convention
 *  `client.ts` uses for `getAllDeals`. Id resolution needs every row, not a page of them. */
const FULL_PAGE = 200;

/**
 * The org's owner / company / contact directory, loaded once and shared by every screen that
 * resolves a `*_id` to a display name — deal cards, activity rows, the contacts-for-a-company
 * list. One fetch of each list here instead of a re-fetch per screen (or per row): `Companies`
 * and `Contacts` still page through their own filtered view for their list screens.
 *
 * Mounted once, inside `RequireAuth`, so it only ever fetches for a signed-in session. `reload()`
 * re-fetches all three — called after an owner/company/contact write lands, so a row created just
 * now resolves by name everywhere else without waiting for a full page reload.
 */
export function DirectoryProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<Directory, "reload">>(EMPTY_DIRECTORY);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let active = true;
    Promise.all([
      getOwners(),
      listCompanies({ size: FULL_PAGE }),
      listContacts({ size: FULL_PAGE }),
    ])
      .then(([owners, companies, contacts]) => {
        if (!active) return;
        setState({ owners, companies: companies.items, contacts: contacts.items, status: "success" });
      })
      .catch(() => {
        if (!active) return;
        setState((prev) => ({ ...prev, status: "error" }));
      });
    return () => {
      active = false;
    };
  }, [nonce]);

  return (
    <DirectoryContext.Provider value={{ ...state, reload }}>{children}</DirectoryContext.Provider>
  );
}
