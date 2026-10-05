import { StatusBadge } from "@opsgrids/ui";

import type { VersionState } from "@/api/catalog";

/** `draft` and `published` are shared status words; `grandfathered` is not one, so it is named
 *  by tone and label — neutral, because a withdrawn version is off sale (it still bills its pins). */
export function VersionStateBadge({ state }: { state: VersionState }) {
  if (state === "grandfathered") return <StatusBadge tone="neutral" label="Withdrawn" />;
  return <StatusBadge status={state} />;
}
