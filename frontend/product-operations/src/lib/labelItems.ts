import type { LabelItem } from "@/api/scan";
import { code128Encodable } from "@/lib/scanPayload";

/** Why a record cannot be printed, or `""`. A code outside printable ASCII has no Code 128. */
export function unprintableReason(item: LabelItem): string {
  return code128Encodable(item.code) && item.code.length <= 64
    ? ""
    : "Its code has characters a bar code cannot carry";
}
