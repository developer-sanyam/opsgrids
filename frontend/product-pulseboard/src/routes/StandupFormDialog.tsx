import { useRef, useState } from "react";

import { postStandup } from "@/api/client";
import type { Person, StandupEntry } from "@/api/types";
import { withOptional } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, Switch, TextArea, useFormDirty } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

/**
 * Post or edit one person's check-in. Reposting the same person and date is the edit — the
 * server treats it as an upsert, so this dialog doesn't need a separate update path.
 */
export function StandupFormDialog({
  entry,
  people,
  date,
  defaultPersonId,
  onClose,
  onSaved,
}: {
  /** `null` posts a new check-in. */
  entry: StandupEntry | null;
  people: Person[];
  date: string;
  defaultPersonId?: string;
  onClose: () => void;
  onSaved: (entry: StandupEntry, message: string) => void;
}) {
  const editing = entry !== null;
  const [personRef, setPersonRef] = useState(entry?.person_id ?? defaultPersonId ?? people[0]?.id ?? "");
  const [yesterday, setYesterday] = useState(entry?.yesterday ?? "");
  const [today, setToday] = useState(entry?.today ?? "");
  const [blocked, setBlocked] = useState(entry?.blockers !== null && entry?.blockers !== undefined);
  const [blockers, setBlockers] = useState(entry?.blockers ?? "");

  const fields = { personRef, yesterday, today, blocked, blockers };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [personError, setPersonError] = useState<string | undefined>(undefined);
  const personRef2 = useRef<HTMLSelectElement>(null);

  const save = useMutation(
    () => {
      const person = people.find((p) => p.id === personRef);
      if (!person) throw new Error("Choose someone from the roster.");
      return postStandup({
        person_ref: person.id,
        person_id: person.uuid,
        date,
        yesterday: yesterday.trim(),
        today: today.trim(),
        blockers: blocked ? blockers.trim() || "Blocked" : null,
      });
    },
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, editing ? "Check-in updated." : "Check-in posted.");
      },
    },
  );

  function submit() {
    if (personRef.trim() === "") {
      setPersonError("Choose who this check-in is for.");
      personRef2.current?.focus();
      return;
    }
    setPersonError(undefined);
    void save.mutate(undefined);
  }

  const personOptions: SelectOption[] = people.map((p) => ({ value: p.id, label: p.name }));
  const nonFieldError =
    save.error && Object.keys(save.fieldErrors).length === 0 ? problemMessage(save.error) : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? "Edit check-in" : "Post check-in"}
      submitLabel={editing ? "Save changes" : "Post check-in"}
      pendingLabel={editing ? "Saving…" : "Posting…"}
      pending={save.pending}
      initialFocusRef={personRef2}
      size="lg"
      dirty={dirty}
      {...withOptional("error", nonFieldError)}
      {...withOptional("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      <Select
        ref={personRef2}
        label="Person"
        value={personRef}
        onChange={(e) => setPersonRef(e.target.value)}
        options={personOptions}
        disabled={editing}
        required
        {...withOptional("error", personError ?? save.fieldErrors.person_ref)}
      />
      <TextArea
        label="Yesterday"
        value={yesterday}
        onChange={(e) => setYesterday(e.target.value)}
        maxLength={4000}
        required
        {...withOptional("error", save.fieldErrors.yesterday)}
      />
      <TextArea
        label="Today"
        value={today}
        onChange={(e) => setToday(e.target.value)}
        maxLength={4000}
        required
        {...withOptional("error", save.fieldErrors.today)}
      />
      <Switch label="Something is blocking this" checked={blocked} onChange={setBlocked} />
      {blocked ? (
        <TextArea
          label="Blockers"
          value={blockers}
          onChange={(e) => setBlockers(e.target.value)}
          maxLength={4000}
          helper="What's in the way, so someone can help unblock it."
          {...withOptional("error", save.fieldErrors.blockers)}
        />
      ) : null}
    </FormDialog>
  );
}
