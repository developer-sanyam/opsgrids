import { useState } from "react";

import { ConfirmDialog } from "./ConfirmDialog";
import { FormDialog } from "./FormDialog";
import { Menu } from "./Menu";
import type { MenuItem } from "./Menu";
import { RadioGroup } from "./RadioGroup";
import { TextField } from "./TextField";

export type SavedViewVisibility = "private" | "org";

export interface SavedViewItem {
  id: string;
  name: string;
  visibility: SavedViewVisibility;
  /** The person viewing created it, so they may update or delete it. */
  mine: boolean;
  /** A preset the app ships (not stored); never updated or deleted. */
  system?: boolean | undefined;
}

export interface SavedViewMenuProps {
  views: SavedViewItem[];
  activeId: string | null;
  /** The list's filters differ from the active view's. */
  dirty: boolean;
  /** `null` is "All": no view. */
  onSelect: (id: string | null) => void;
  onSave: (name: string, visibility: SavedViewVisibility) => void;
  onUpdate: (id: string) => void;
  onDelete: (id: string) => void;
  /** May share a view with the organization. Presentation only: the server decides. */
  canShare: boolean;
}

const NAME_MAX = 80;

/**
 * The saved views of a list, behind one "Views" menu: All, the app's presets, the person's own
 * views, then views shared with them. "Save as view…" names the current filters; "Update view"
 * and "Delete view" act on the person's own active view only, and Delete asks first.
 *
 * Presentational: the app owns the views and the URL they set (`useSavedViews`, R3.8).
 */
export function SavedViewMenu({
  views,
  activeId,
  dirty,
  onSelect,
  onSave,
  onUpdate,
  onDelete,
  canShare,
}: SavedViewMenuProps) {
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<SavedViewItem | null>(null);
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState<SavedViewVisibility>("private");
  const [nameError, setNameError] = useState<string | undefined>(undefined);

  const active = views.find((view) => view.id === activeId) ?? null;
  const editable = active !== null && active.mine && active.system !== true;
  const ordered = [
    ...views.filter((view) => view.system === true),
    ...views.filter((view) => view.system !== true && view.mine),
    ...views.filter((view) => view.system !== true && !view.mine),
  ];

  const items: MenuItem[] = [
    { label: "All", onSelect: () => onSelect(null) },
    ...ordered.map((view) => ({
      label: view.system !== true && !view.mine ? `${view.name} (shared)` : view.name,
      onSelect: () => onSelect(view.id),
    })),
    {
      label: "Save as view…",
      onSelect: () => {
        setName("");
        setVisibility("private");
        setNameError(undefined);
        setSaving(true);
      },
    },
    { label: "Update view", hidden: !(editable && dirty), onSelect: () => active !== null && onUpdate(active.id) },
    { label: "Delete view", tone: "danger", hidden: !editable, onSelect: () => setDeleting(active) },
  ];

  function save() {
    const trimmed = name.trim();
    if (trimmed.length < 1 || trimmed.length > NAME_MAX) {
      setNameError(`Name the view in 1 to ${NAME_MAX} characters.`);
      return;
    }
    onSave(trimmed, canShare ? visibility : "private");
    setSaving(false);
  }

  return (
    <div className="inline-flex items-center gap-2">
      <Menu label={active !== null ? `Views: ${active.name}` : "Views"} items={items} />
      {dirty ? (
        <span className="inline-flex items-center">
          <span aria-hidden className="size-2 rounded-full bg-accent" />
          <span className="sr-only">Unsaved changes</span>
        </span>
      ) : null}

      <FormDialog
        open={saving}
        onClose={() => setSaving(false)}
        onSubmit={save}
        title="Save view"
        description="Saves the current filters and sort under a name."
        submitLabel="Save view"
        size="sm"
        dirty={name !== ""}
      >
        <TextField
          label="Name"
          required
          maxLength={NAME_MAX}
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setNameError(undefined);
          }}
          error={nameError}
        />
        {canShare ? (
          <RadioGroup
            legend="Who can see it"
            name="saved-view-visibility"
            value={visibility}
            onChange={(value) => setVisibility(value === "org" ? "org" : "private")}
            options={[
              { value: "private", label: "Only me" },
              { value: "org", label: "Everyone in the organization" },
            ]}
          />
        ) : null}
      </FormDialog>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          if (deleting !== null) onDelete(deleting.id);
          setDeleting(null);
        }}
        title={deleting !== null ? `Delete “${deleting.name}”?` : "Delete this view?"}
        body="The saved filters are deleted. The records they showed are not changed."
        confirmLabel="Delete view"
      />
    </div>
  );
}
