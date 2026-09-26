import { useCallback } from "react";

import { useAsync } from "@opsgrids/client";

import { getWhatsAppConnection } from "@/api/client";

/**
 * The connected WhatsApp Business Account's ID, from the connection read that already exists.
 * Best effort by design: the ID only helps a person recognise the account, so a failed or empty
 * read shows nothing rather than an error over a screen that does not depend on it.
 */
export function useWabaId(): string | null {
  const loader = useCallback(() => getWhatsAppConnection(), []);
  const state = useAsync(loader, []);
  return state.status === "success" ? (state.data.items[0]?.waba_id ?? null) : null;
}
