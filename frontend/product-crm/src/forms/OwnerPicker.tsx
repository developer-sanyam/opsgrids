import { useContext, useState } from "react";

import { createOwner } from "@/api/client";
import type { Owner } from "@/api/types";
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
 * unblock the very first deal they try to create is the kind of friction that reads as a bug —
 * so the caller who holds `crm.book.owner.write` at ORG gets a one-click "make me the owner"
 * built from their own session, with the full multi-field form kept one tap away for the case
 * that is actually someone else.
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
  // The service requires a non-empty name, so a session without one gets the full form instead
  // of a button that reads "Make  the owner" and can only be refused.
  const claimant = user !== null && user.full_name.trim() !== "" ? user : null;

  const claimOwner = useMutation(
    () =>
      createOwner({
        full_name: claimant?.full_name.trim() ?? "",
        email: claimant?.email ?? "",
      }),
    { onSuccess: (owner) => onOwnerCreated(owner) },
  );

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
          className="w-fit text-xs font-semibold text-accent hover:text-accent-strong transition-colors"
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
        {...(error !== undefined ? { error } : {})}
        {...(disabled !== undefined ? { disabled } : {})}
      />
      {canAddOwner ? (
        <button
          type="button"
          onClick={() => setAddingOwner(true)}
          className="w-fit text-xs font-semibold text-accent hover:text-accent-strong transition-colors"
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
