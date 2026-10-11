import { describe, expect, it } from "vitest";

import { requisitionActions } from "./requisitionActions";
import type { RequisitionPermissions } from "./requisitionActions";

const ALL: RequisitionPermissions = { canWrite: true, canApprove: true, canWritePurchaseOrder: true };
const NONE: RequisitionPermissions = { canWrite: false, canApprove: false, canWritePurchaseOrder: false };

describe("requisitionActions", () => {
  it("offers a requester edit, submit and cancel on a draft, and no decision", () => {
    expect(requisitionActions("DRAFT", { ...NONE, canWrite: true })).toEqual({
      edit: true,
      submit: true,
      approve: false,
      reject: false,
      cancel: true,
      convert: "hidden",
    });
  });

  it("offers approve and reject on a submitted requisition only to an approver", () => {
    const requester = requisitionActions("SUBMITTED", { ...NONE, canWrite: true });
    expect(requester.approve).toBe(false);
    expect(requester.reject).toBe(false);
    expect(requester.cancel).toBe(true);
    const approver = requisitionActions("SUBMITTED", { ...NONE, canApprove: true });
    expect(approver.approve).toBe(true);
    expect(approver.reject).toBe(true);
    expect(approver.cancel).toBe(false);
  });

  it("needs both write permissions to convert, and explains by disabling when one is missing", () => {
    expect(requisitionActions("APPROVED", ALL).convert).toBe("enabled");
    expect(requisitionActions("APPROVED", { ...ALL, canWritePurchaseOrder: false }).convert).toBe("disabled");
    expect(requisitionActions("APPROVED", { ...ALL, canWrite: false }).convert).toBe("hidden");
  });

  it("offers nothing on a finished requisition", () => {
    for (const status of ["REJECTED", "CONVERTED", "CANCELLED"] as const) {
      const a = requisitionActions(status, ALL);
      expect(Object.values(a).some((v) => v === true || v === "enabled" || v === "disabled")).toBe(false);
    }
  });

  it("offers nothing to someone with no permissions", () => {
    for (const status of ["DRAFT", "SUBMITTED", "APPROVED"] as const) {
      const a = requisitionActions(status, NONE);
      expect(Object.values(a).some((v) => v === true || v === "enabled" || v === "disabled")).toBe(false);
    }
  });
});
