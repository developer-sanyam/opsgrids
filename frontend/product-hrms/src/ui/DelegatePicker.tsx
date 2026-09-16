/**
 * The one person picker in this app, over `GET /approvals/delegate-candidates?q=` (§4.E).
 *
 * Both of the delegation form's fields and the delegations list's person filter render it, so the
 * endpoint, its copy and its keyboard contract are stated once. A second picker over a second
 * endpoint is how two screens end up disagreeing about who a colleague is.
 *
 * What the endpoint decides, and this component therefore never re-decides: the caller themselves
 * and anyone who has left are not candidates, matching is a prefix of a name or an employee
 * number, `q` must be at least two characters, and the result set is capped at 20.
 */

import { Combobox } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

import { listDelegateCandidates } from "@/api/approvals";
import type { DelegateCandidateOut } from "@/api/types";

/** §4.E's three pre-result states, in this screen's nouns. The rest of `ComboboxCopy` already
 *  reads the way the spec writes it, so it is left at the design system's default. */
const DELEGATE_COPY = {
  idle: "Start typing a colleague's name.",
  error: "Couldn't load colleagues.",
};

/** A candidate as the picker's option: name over employee number, the **block** PersonRef the
 *  spec asks for wherever a person is being *identified* (§2.2). */
function candidateOption(candidate: DelegateCandidateOut): ComboboxOption {
  return {
    id: candidate.employee_id,
    label: candidate.display_name,
    sublabel: candidate.employee_no,
  };
}

interface DelegatePickerProps {
  label: string;
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  helper?: string;
  error?: string;
  placeholder?: string;
}

export function DelegatePicker({
  label,
  value,
  onChange,
  helper,
  error,
  placeholder = "Search for a colleague…",
}: DelegatePickerProps) {
  return (
    <Combobox
      label={label}
      value={value}
      onChange={onChange}
      loadOptions={async (q, signal) => (await listDelegateCandidates(q, signal)).map(candidateOption)}
      placeholder={placeholder}
      copy={DELEGATE_COPY}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
