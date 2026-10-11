import { describe, expect, it } from "vitest";

import type { ApprovalRoute, TransferStatus } from "@/api/movementTypes";
import { hasTransferAction, transferActions } from "./transferActions";
import type { TransferPermissions } from "./transferActions";

const ALL: TransferPermissions = { write: true, approve: true, dispatch: true, receive: true };
const NONE: TransferPermissions = { write: false, approve: false, dispatch: false, receive: false };

function offered(status: TransferStatus, route: ApprovalRoute | null, can: TransferPermissions): string[] {
  return Object.entries(transferActions(status, route, can))
    .filter(([, on]) => on)
    .map(([name]) => name);
}

describe("transferActions", () => {
  it("offers a draft its request, edit and cancel", () => {
    expect(offered("DRAFT", null, ALL)).toEqual(["request", "edit", "cancel"]);
  });

  it("offers a direct-route request a decision and a cancel", () => {
    expect(offered("REQUESTED", "DIRECT", ALL)).toEqual(["approve", "reject", "cancel"]);
  });

  it("never offers a decision on the approval-service route, or when nothing needs deciding", () => {
    expect(offered("REQUESTED", "SERVICE", ALL)).toEqual(["cancel"]);
    expect(offered("REQUESTED", "AUTO", ALL)).toEqual(["cancel"]);
  });

  it("offers an approved transfer its dispatch and a cancel", () => {
    expect(offered("APPROVED", "AUTO", ALL)).toEqual(["cancel", "dispatch"]);
  });

  it("offers an in-transit transfer only a receipt — it can no longer be cancelled", () => {
    expect(offered("IN_TRANSIT", "AUTO", ALL)).toEqual(["receive"]);
  });

  it.each<TransferStatus>(["RECEIVED", "CANCELLED", "REJECTED"])("offers nothing once %s", (status) => {
    expect(offered(status, "DIRECT", ALL)).toEqual([]);
  });

  it("offers nothing a viewer does not hold the permission for", () => {
    expect(offered("DRAFT", null, NONE)).toEqual([]);
    expect(offered("REQUESTED", "DIRECT", NONE)).toEqual([]);
    expect(offered("APPROVED", "AUTO", NONE)).toEqual([]);
    expect(offered("IN_TRANSIT", "AUTO", NONE)).toEqual([]);
  });

  it("gates each action on its own permission", () => {
    expect(offered("APPROVED", "AUTO", { ...NONE, dispatch: true })).toEqual(["dispatch"]);
    expect(offered("IN_TRANSIT", "AUTO", { ...NONE, receive: true })).toEqual(["receive"]);
    expect(offered("REQUESTED", "DIRECT", { ...NONE, approve: true })).toEqual(["approve", "reject"]);
    expect(offered("REQUESTED", "DIRECT", { ...NONE, write: true })).toEqual(["cancel"]);
  });

  it("reports whether any control is on offer", () => {
    expect(hasTransferAction(transferActions("RECEIVED", null, ALL))).toBe(false);
    expect(hasTransferAction(transferActions("DRAFT", null, ALL))).toBe(true);
  });
});
