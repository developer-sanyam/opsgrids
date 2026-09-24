/**
 * The picker for "which member is this employee?" — `GET /members?linked=false&q=` on the account
 * service (ADR-0008 §9), the same list an administrator uses to find the people whose approvals
 * inbox is silently empty. It offers only members with **no employee link yet**: one employee, one
 * member, and the server's 409 covers the race where somebody else links them first.
 *
 * Each option is the person's name over their **masked** sign-in email (`@/lib/mask`) — enough to
 * tell two people with the same name apart, and not something to leave printed whole on a screen an
 * HR administrator walks away from.
 */

import { Combobox } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

import { MEMBER_PICKER_SIZE, listUnlinkedMembers } from "@/api/members";
import { maskEmail } from "@/lib/mask";

const MEMBER_COPY = {
  idle: "Start typing a member's name or email.",
  error: "Couldn't load members.",
  noMatchesHint: "Only members who aren't linked to an employee yet are listed.",
};

interface MemberPickerProps {
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  disabled?: boolean | undefined;
  error?: string | undefined;
}

export function MemberPicker({ value, onChange, disabled, error }: MemberPickerProps) {
  return (
    <Combobox
      label="Member to link"
      value={value}
      onChange={onChange}
      loadOptions={async (q) => {
        const result = await listUnlinkedMembers({ q, size: MEMBER_PICKER_SIZE });
        return result.items.map((m) => ({
          id: m.id,
          label: m.display_name ?? m.full_name,
          sublabel: maskEmail(m.email),
        }));
      }}
      placeholder="Search for a member…"
      cap={MEMBER_PICKER_SIZE}
      copy={MEMBER_COPY}
      {...(disabled !== undefined ? { disabled } : {})}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
