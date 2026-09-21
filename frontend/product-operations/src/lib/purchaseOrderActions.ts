import type { PoStatus } from "../api/types";

/**
 * Which lifecycle controls a purchase order offers, by status and by whether the viewer holds
 * the approve permission. This mirrors the service's rules so nobody is handed a button that can
 * only be refused; the service remains the thing that enforces them.
 *
 * - Approval is a prerequisite for receiving: a draft cannot be received.
 * - Cancelling a draft undoes nothing. Cancelling an approved order reverses spend an approver
 *   committed, so it needs the approve permission as well.
 */
export interface PurchaseOrderActions {
  edit: boolean;
  approve: boolean;
  receive: boolean;
  cancel: boolean;
  close: boolean;
}

export function purchaseOrderActions(status: PoStatus, canApprove: boolean): PurchaseOrderActions {
  return {
    edit: status === "draft",
    approve: status === "draft" && canApprove,
    receive: status === "approved",
    cancel: status === "draft" || (status === "approved" && canApprove),
    close: status === "received",
  };
}
