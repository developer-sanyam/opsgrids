import type { ApprovalRoute, TransferStatus } from "@/api/movementTypes";

/** What the viewer holds, at `ORG` scope — the transfer permissions all floor there. */
export interface TransferPermissions {
  /** `operations.inventory.transfer.write` */
  write: boolean;
  /** `operations.inventory.transfer.approve` */
  approve: boolean;
  /** `operations.inventory.transfer.dispatch` */
  dispatch: boolean;
  /** `operations.inventory.transfer.receive` */
  receive: boolean;
}

export interface TransferActions {
  request: boolean;
  edit: boolean;
  approve: boolean;
  reject: boolean;
  cancel: boolean;
  dispatch: boolean;
  receive: boolean;
}

/**
 * Which controls a transfer offers, by status, approval route and what the viewer may do —
 * mirroring `TRANSFER_TRANSITIONS` and the checks in `app/movements/transfers.py` so a person is
 * never shown a button that would only 409. The server still decides:
 *
 * - Request and Edit are a DRAFT's, behind `transfer.write`.
 * - Approve and Reject exist only for a REQUESTED transfer on the DIRECT route. On the SERVICE
 *   route the approval service decides (the screen says who, and offers nothing); on AUTO there
 *   is nothing to decide. The requester can never approve their own — that is the server's 409,
 *   shown as it arrives, not predicted here.
 * - Cancel is open until dispatch (a dispatched transfer is received, or its shortage declared).
 * - Dispatch needs APPROVED; Receive needs IN_TRANSIT, and may be repeated for partial receipts.
 */
export function transferActions(
  status: TransferStatus,
  route: ApprovalRoute | null,
  can: TransferPermissions,
): TransferActions {
  const decidable = status === "REQUESTED" && route === "DIRECT" && can.approve;
  return {
    request: status === "DRAFT" && can.write,
    edit: status === "DRAFT" && can.write,
    approve: decidable,
    reject: decidable,
    cancel: (status === "DRAFT" || status === "REQUESTED" || status === "APPROVED") && can.write,
    dispatch: status === "APPROVED" && can.dispatch,
    receive: status === "IN_TRANSIT" && can.receive,
  };
}

/** Whether any control at all is on offer — false means the action bar is not drawn. */
export function hasTransferAction(actions: TransferActions): boolean {
  return Object.values(actions).some(Boolean);
}
