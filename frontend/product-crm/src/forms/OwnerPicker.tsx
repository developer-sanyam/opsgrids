import { useContext, useEffect, useState } from "react";

import { ensureMyOwner } from "@/api/client";
import type { Owner } from "@/api/types";
import { findMyRep, repSeed } from "@/lib/myRep";
import { maybe } from "@/lib/optional";
import { ApiError, AuthContext, problemMessage, useCan, useMutation } from "@opsgrids/client";
import { Button, InlineAlert, Select } from "@opsgrids/ui";
import { OwnerFormDialog } from "./OwnerFormDialog";

interface OwnerPickerProps {
  owners: Owner[];
  value: string;
  onChange: (ownerId: string) => void;
  error?: string;
  /** Called after a first-run "add your first team member" create, so the caller can refresh the
   *  shared directory (the new owner needs to resolve by name everywhere else too). */
  onOwnerCreated: (owner: Owner) => void;
  disabled?: boolean;
}

/**
 * The owner picker every create dialog needs. A production org starts with **zero** owners, and
 * every company / contact / deal create requires one.
 *
 * The empty directory is also, almost always, one person: whoever just signed up is the org
 * owner and the first rep. Asking them to fill in their own name and email a second time to
 * unblock the very first deal they try to create is the kind of friction that reads as a bug --
 * so the caller who holds `crm.book.owner.write` at ORG gets a one-click "make me the owner"
 * built from their own session (`PUT /customers/owners/me`, idempotent), with the full
 * multi-field form kept one tap away for the case that is actually someone else.
 *
 * **The owner is never left blank when it is obvious.** When the picker has nothing chosen, it
 * chooses the signed-in login's own rep record, or the only active rep there is. A Select showing
 * a name while its value is still empty is what produced "Choose an owner" beside an owner that
 * looked chosen.
 */
export function OwnerPicker({ owners, value, onChange, error, onOwnerCreated, disabled }: OwnerPickerProps) {
  const [addingOwner, setAddingOwner] = useState(false);
  // Adding a rep is an organization-wide act: the service refuses anything narrower, so a rep
  // who may only work their own book is told who can do it rather than offered a button that
  // can only be refused.
  const canAddOwner = useCan("crm.book.owner.write", "ORG");
  // `useContext` rather than the throwing `useAuth()`: every real render sits under this
  // product's `<AuthProvider>`, but a picker rendered in isolation (a story, a narrower test)
  // should degrade to "no one-click claim on offer" rather than crash.
  const user = useContext(AuthContext)?.user ?? null;
  const seed = repSeed(user);
  // The service requires a non-empty name, so a session without one gets the full form instead
  // of a button that reads "Make  the owner" and can only be refused.
  const claimant = seed !== null && user !== null ? user : null;
  const myRep = findMyRep(owners, user);
  const activeOwners = owners.filter((o) => o.is_active);

  const claimOwner = useMutation(
    () => ensureMyOwner({ full_name: seed?.full_name ?? "", email: seed?.email ?? "" }),
    { onSuccess: (owner) => onOwnerCreated(owner) },
  );

  // Choose the obvious owner when nothing is chosen yet.
  const only = activeOwners.length === 1 ? activeOwners[0] : undefined;
  const obvious = myRep !== undefined && myRep.is_active ? myRep : only;
  const obviousId = obvious?.id ?? "";
  useEffect(() => {
    if (value === "" && obviousId !== "" && disabled !== true) onChange(obviousId);
    // `onChange` is the caller's setter and changes identity each render; only a changed
    // choice or an emptied value should re-run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, obviousId, disabled]);

  if (owners.length === 0 && !canAddOwner) {
    return (
      <InlineAlert tone="info" live="off" title="No team members yet">
        Every record needs an owner, and this organization has none yet. Ask an administrator to add
        the team on the Team page.
      </InlineAlert>
    );
  }

  if (owners.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <InlineAlert tone="info" live="off" title="No team members yet">
          Every record needs an owner. Make yourself the owner to continue, or add a different team
          member.
        </InlineAlert>
        {claimOwner.error ? (
          <InlineAlert
            tone="danger"
            title="Couldn't make you the owner"
            {...maybe("reference", (claimOwner.error as ApiError).problem?.request_id)}
          >
            {problemMessage(claimOwner.error)}
          </InlineAlert>
        ) : null}
        {claimant ? (
          // Outline, not primary: the dialog this sits in already has its own primary submit.
          <Button
            type="button"
            variant="outline"
            loading={claimOwner.pending}
            onClick={() => void claimOwner.mutate(undefined)}
          >
            Make {claimant.full_name.trim()} the owner
          </Button>
        ) : null}
        <button
          type="button"
          onClick={() => setAddingOwner(true)}
          className="relative hit-44 w-fit text-xs font-semibold text-accent hover:text-accent-strong transition-colors"
        >
          Add a different team member
        </button>
        {addingOwner ? (
          <OwnerFormDialog
            onClose={() => setAddingOwner(false)}
            onSaved={(owner) => {
              setAddingOwner(false);
              onOwnerCreated(owner);
            }}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <Select
        label="Owner"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Choose an owner"
        options={owners.map((o) => ({ value: o.id, label: o.full_name }))}
        required
        {...(error !== undefined ? { error } : {})}
        {...(disabled !== undefined ? { disabled } : {})}
      />
      {claimOwner.error ? (
        <InlineAlert
          tone="danger"
          title="Couldn't add you as a rep"
          {...maybe("reference", (claimOwner.error as ApiError).problem?.request_id)}
        >
          {problemMessage(claimOwner.error)}
        </InlineAlert>
      ) : null}
      {canAddOwner && claimant !== null && myRep === undefined ? (
        // The roster has other people but not this login: one tap puts them on it.
        <button
          type="button"
          disabled={claimOwner.pending}
          onClick={() => void claimOwner.mutate(undefined)}
          className="relative hit-44 w-fit text-xs font-semibold text-accent hover:text-accent-strong transition-colors disabled:opacity-60"
        >
          {claimOwner.pending ? "Adding you…" : "+ Add me as a sales rep"}
        </button>
      ) : null}
      {canAddOwner ? (
        <button
          type="button"
          onClick={() => setAddingOwner(true)}
          className="relative hit-44 w-fit text-xs font-semibold text-accent hover:text-accent-strong transition-colors"
        >
          + Add a new team member
        </button>
      ) : null}
      {addingOwner ? (
        <OwnerFormDialog
          onClose={() => setAddingOwner(false)}
          onSaved={(owner) => {
            setAddingOwner(false);
            onOwnerCreated(owner);
          }}
        />
      ) : null}
    </div>
  );
}
