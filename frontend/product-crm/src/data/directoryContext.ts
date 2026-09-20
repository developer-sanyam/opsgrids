/**
 * The directory context and its hook.
 *
 * Kept in a `.ts` file with no component export so React Fast Refresh stays happy — the
 * provider lives next door in `DirectoryProvider.tsx`, the same split `@opsgrids/client` uses
 * for `auth/context.ts` / `AuthProvider.tsx`.
 */
import { createContext, useContext } from "react";

import type { Company, Contact, Owner } from "@/api/types";

export interface Directory {
  owners: Owner[];
  companies: Company[];
  contacts: Contact[];
  /**
   * Whether the directory has loaded at least once. Every lookup in `data/lookups.ts` degrades
   * to its "unknown" fallback ("Unassigned", "—") while this is `"loading"` or `"error"` — never
   * fixture data, and never a thrown error on a screen whose own record loaded fine. The record
   * itself (a deal, a company, a contact) carries its own loading/error state independently;
   * this directory only resolves the *other* ids a record references.
   */
  status: "loading" | "success" | "error";
}

export const EMPTY_DIRECTORY: Directory = {
  owners: [],
  companies: [],
  contacts: [],
  status: "loading",
};

export const DirectoryContext = createContext<Directory | null>(null);

/** Read the shared directory. Must be called under a `DirectoryProvider` (mounted in `App.tsx`). */
export function useDirectory(): Directory {
  const directory = useContext(DirectoryContext);
  if (!directory) throw new Error("useDirectory must be used within a DirectoryProvider");
  return directory;
}
