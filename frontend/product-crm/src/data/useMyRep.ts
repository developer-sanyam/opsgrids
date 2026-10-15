import { useCallback, useContext } from "react";

import { ensureMyOwner } from "@/api/client";
import type { Owner } from "@/api/types";
import { findMyRep, repSeed } from "@/lib/myRep";
import { AuthContext, useCan } from "@opsgrids/client";

import { useDirectory } from "./directoryContext";

export interface MyRep {
  /** The roster row that is the signed-in login, or `undefined` when they are not on it yet. */
  rep: Owner | undefined;
  /**
   * The login's rep record, putting them on the roster first when they hold org-level owner write
   * and are not on it (`PUT /customers/owners/me`, idempotent). `undefined` when they are not on
   * it and may not add themselves -- the caller then lets the server's own default apply.
   *
   * This is what lets the organization owner, who has no employee record and so no `sref`, log an
   * activity or create a record without being asked to build a team first.
   */
  ensure: () => Promise<Owner | undefined>;
}

export function useMyRep(): MyRep {
  const { owners, reload } = useDirectory();
  const user = useContext(AuthContext)?.user ?? null;
  const canAdd = useCan("crm.book.owner.write", "ORG");
  const rep = findMyRep(owners, user);
  const seed = repSeed(user);

  const ensure = useCallback(async (): Promise<Owner | undefined> => {
    if (rep !== undefined) return rep;
    if (!canAdd || seed === null) return undefined;
    const created = await ensureMyOwner(seed);
    reload();
    return created;
  }, [rep, canAdd, seed?.email, seed?.full_name, reload]); // eslint-disable-line react-hooks/exhaustive-deps

  return { rep, ensure };
}
