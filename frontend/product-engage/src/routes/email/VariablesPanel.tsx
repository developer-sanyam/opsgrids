import { useState } from "react";

import { Button, SearchInput, TextField } from "@/ui";

import { isValidVariableName, placeholderToken } from "./templateText";

/**
 * The variables a template can name, as buttons that insert `{{name}}` at the caret.
 *
 * The list is the placeholders this template already uses plus the ones the organization's other
 * email templates use, read from the service's own derived `placeholders`. There is no catalogue
 * endpoint for email variables, so a name that appears nowhere yet is added by typing it: the
 * service accepts any placeholder in its grammar, and the name is checked against that grammar
 * here before it is offered.
 */
export function VariablesPanel({
  variables,
  onInsert,
}: {
  variables: string[];
  /** Insert `{{name}}` into whichever field the person last had the caret in. */
  onInsert: (name: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);

  const needle = search.trim().toLowerCase();
  const shown = variables.filter((name) => name.toLowerCase().includes(needle));

  function add() {
    const name = draft.trim();
    if (!isValidVariableName(name)) {
      setError("Use letters, digits, underscores and dots, like customer_name.");
      return;
    }
    setError(undefined);
    setDraft("");
    onInsert(name);
  }

  return (
    <section aria-labelledby="template-variables" className="flex flex-col gap-3">
      <h2 id="template-variables" className="text-sm font-bold text-ink">
        Variables
      </h2>
      <SearchInput
        label="Search variables"
        placeholder="Search variables..."
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {shown.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {shown.map((name) => (
            <li key={name}>
              <button
                type="button"
                onClick={() => onInsert(name)}
                aria-label={`Insert ${placeholderToken(name)}`}
                className="relative hit-44 flex w-full items-center justify-between gap-2 rounded-tile border border-line px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-ground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <span className="font-semibold">{name}</span>
                <span className="text-xs text-muted">Text</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">
          {variables.length === 0
            ? "No variables yet. Add one below and it is inserted where your cursor is."
            : "No variables match that search."}
        </p>
      )}
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          add();
        }}
      >
        <TextField
          label="New variable name"
          placeholder="customer_name"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setError(undefined);
          }}
          error={error}
        />
        <Button type="submit" variant="secondary" size="sm">
          Add Variable
        </Button>
      </form>
      <p className="text-xs text-muted">
        A variable is filled in from the event that triggers the message. Insert it wherever you want
        the value to appear.
      </p>
    </section>
  );
}
