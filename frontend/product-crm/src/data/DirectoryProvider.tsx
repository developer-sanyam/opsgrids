import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { getOwners, listCompanies, listContacts } from "@/api/client";
import { DirectoryContext, EMPTY_DIRECTORY } from "./directoryContext";
import type { Directory } from "./directoryContext";

/**
 * The org's owner / company / contact directory, loaded once and shared by every screen that
 * resolves a `*_id` to a display name — deal cards, activity rows, the contacts-for-a-company
 * list. One fetch of each list here instead of a re-fetch per screen (or per row): `Companies`
 * and `Contacts` still page through their own filtered view for their list screens, but id
 * resolution needs the whole (small) book, the same "generous page size" convention `client.ts`
 * already uses for `getAllDeals`.
 *
 * Mounted once, inside `RequireAuth`, so it only ever fetches for a signed-in session.
 */
export function DirectoryProvider({ children }: { children: ReactNode }) {
  const [directory, setDirectory] = useState<Directory>(EMPTY_DIRECTORY);

  useEffect(() => {
    let active = true;
    Promise.all([getOwners(), listCompanies(), listContacts()])
      .then(([owners, companies, contacts]) => {
        if (!active) return;
        setDirectory({ owners, companies: companies.items, contacts: contacts.items, status: "success" });
      })
      .catch(() => {
        if (!active) return;
        setDirectory((prev) => ({ ...prev, status: "error" }));
      });
    return () => {
      active = false;
    };
  }, []);

  return <DirectoryContext.Provider value={directory}>{children}</DirectoryContext.Provider>;
}
