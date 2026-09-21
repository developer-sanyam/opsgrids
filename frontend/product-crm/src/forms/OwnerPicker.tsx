import { useState } from "react";

import type { Owner } from "@/api/types";
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
 * every company / contact / deal create requires one — so instead of a Select with no options,
 * a caller with an empty directory gets an inline path to add the first team member without
 * leaving the dialog they came to fill in.
 */
export function OwnerPicker({ owners, value, onChange, error, onOwnerCreated, disabled }: OwnerPickerProps) {
  const [addingOwner, setAddingOwner] = useState(false);

  if (owners.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <InlineAlert tone="info" live="off" title="No team members yet">
          Every record needs an owner. Add your first team member to continue.
        </InlineAlert>
        <Button type="button" variant="outline" onClick={() => setAddingOwner(true)}>
          Add your first team member
        </Button>
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
      <button
        type="button"
        onClick={() => setAddingOwner(true)}
        className="w-fit text-xs font-semibold text-accent hover:text-accent-strong transition-colors"
      >
        + Add a new team member
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
