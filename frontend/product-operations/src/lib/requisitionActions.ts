import type { RequisitionStatus } from "../api/types";

/**
 * Which controls a requisition offers, by status and by what the viewer holds. This mirrors the
 * service so nobody is handed a button that can only be refused; the service enforces every one.
 *
 * - Edit and submit are the requester's, on a draft.
 * - Approve and reject are the approver's (`requisition.approve`, floored at ORG on the server so
 *   a requester can never approve their own request), on a submitted requisition.
 * - Cancel is open until the requisition has been converted or decided against.
 * - Convert turns an approved requisition into a draft purchase order, so it needs
 *   `requisition.write` **and** `purchase_order.write`. A buyer who can write requisitions but not
 *   purchase orders sees it disabled, with the reason, rather than not at all.
 */
export interface RequisitionPermissions {
  canWrite: boolean;
  canApprove: boolean;
  canWritePurchaseOrder: boolean;
}

export type ConvertControl = "hidden" | "disabled" | "enabled";

export interface RequisitionActions {
  edit: boolean;
  submit: boolean;
  approve: boolean;
  reject: boolean;
  cancel: boolean;
  convert: ConvertControl;
}

export function requisitionActions(status: RequisitionStatus, can: RequisitionPermissions): RequisitionActions {
  const convertible = status === "APPROVED" && can.canWrite;
  return {
    edit: status === "DRAFT" && can.canWrite,
    submit: status === "DRAFT" && can.canWrite,
    approve: status === "SUBMITTED" && can.canApprove,
    reject: status === "SUBMITTED" && can.canApprove,
    cancel: (status === "DRAFT" || status === "SUBMITTED" || status === "APPROVED") && can.canWrite,
    convert: !convertible ? "hidden" : can.canWritePurchaseOrder ? "enabled" : "disabled",
  };
}
