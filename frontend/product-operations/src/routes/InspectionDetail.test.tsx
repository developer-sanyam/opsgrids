/**
 * One inspection (inventory plan Phase 4): every decision button appears only for the permission
 * that owns it and the state that allows it, a release or rejection sends exactly what was typed
 * (decimal strings, never numbers), and a failed critical parameter blocks release in the UI as
 * well as on the server.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { inspectionFixture } from "@/test-utils/qualityFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { InspectionDetailPage } from "./InspectionDetail";

const getInspection = vi.fn();
const releaseInspection = vi.fn();
const rejectInspection = vi.fn();
const disposeInspection = vi.fn();
const stepInspection = vi.fn();
const recordInspectionResults = vi.fn();
const getQcTemplate = vi.fn();

vi.mock("@/api/client", () => ({
  getInspection: (...a: unknown[]) => getInspection(...a),
  releaseInspection: (...a: unknown[]) => releaseInspection(...a),
  rejectInspection: (...a: unknown[]) => rejectInspection(...a),
  disposeInspection: (...a: unknown[]) => disposeInspection(...a),
  stepInspection: (...a: unknown[]) => stepInspection(...a),
  recordInspectionResults: (...a: unknown[]) => recordInspectionResults(...a),
  getQcTemplate: (...a: unknown[]) => getQcTemplate(...a),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ id: "insp-1" }),
}));

const READ = "operations.quality.inspection.read:ORG";
const INSPECT = "operations.quality.inspection.inspect:ORG";
const APPROVE = "operations.quality.inspection.approve:ORG";
const REJECT = "operations.quality.inspection.reject:ORG";

const FAILED_CRITICAL = {
  id: "r-1",
  param_name: "Shrinkage",
  param_kind: "numeric" as const,
  unit: "%",
  min: null,
  max: "3.5",
  is_critical: true,
  value: "9",
  passed: false,
  note: "",
  recorded_by: "user-1",
};

function button(name: string) {
  return screen.queryByRole("button", { name });
}

describe("InspectionDetailPage", () => {
  beforeEach(() => {
    for (const fn of [
      getInspection,
      releaseInspection,
      rejectInspection,
      disposeInspection,
      stepInspection,
      recordInspectionResults,
      getQcTemplate,
    ]) {
      fn.mockReset();
    }
  });

  it("shows the quantities as the server sent them", async () => {
    getInspection.mockResolvedValue(inspectionFixture());
    renderScreen(<InspectionDetailPage />, [READ]);
    expect(await screen.findByText("40.125 m")).toBeDefined();
    expect(screen.getByText("Pending")).toBeDefined();
    expect(screen.getByText("No readings recorded yet")).toBeDefined();
  });

  it("gives a read-only viewer no action at all", async () => {
    getInspection.mockResolvedValue(inspectionFixture());
    renderScreen(<InspectionDetailPage />, [READ]);
    await screen.findByText("40.125 m");
    for (const name of ["Record readings", "Hold", "Rework", "Release", "Reject", "Dispose", "Re-inspect"]) {
      expect(button(name), name).toBeNull();
    }
  });

  it("gives an inspector the inspection steps but not the decisions", async () => {
    getInspection.mockResolvedValue(inspectionFixture());
    renderScreen(<InspectionDetailPage />, [READ, INSPECT]);
    await screen.findByText("40.125 m");
    expect(button("Record readings")).not.toBeNull();
    expect(button("Hold")).not.toBeNull();
    expect(button("Rework")).not.toBeNull();
    expect(button("Release")).toBeNull();
    expect(button("Reject")).toBeNull();
  });

  it("offers Re-inspect only on rework, and Dispose only for rejected stock not yet disposed of", async () => {
    getInspection.mockResolvedValue(inspectionFixture({ status: "REWORK" }));
    const first = renderScreen(<InspectionDetailPage />, [READ, INSPECT, REJECT]);
    await screen.findByText("Rework");
    expect(button("Re-inspect")).not.toBeNull();
    expect(button("Hold")).toBeNull();
    first.unmount();

    getInspection.mockResolvedValue(
      inspectionFixture({ status: "FAILED", accepted: "0", rejected: "40.125", rejected_bucket: "REJECTED" }),
    );
    const second = renderScreen(<InspectionDetailPage />, [READ, REJECT]);
    expect(await screen.findByRole("button", { name: "Dispose" })).toBeDefined();
    second.unmount();

    getInspection.mockResolvedValue(
      inspectionFixture({
        status: "FAILED",
        accepted: "0",
        rejected: "40.125",
        rejected_bucket: "REJECTED",
        disposition: "SCRAP",
      }),
    );
    renderScreen(<InspectionDetailPage />, [READ, REJECT]);
    await screen.findByText(/Rejected · Scrapped/);
    expect(button("Dispose")).toBeNull();
  });

  it("disables Release and says why while a critical parameter has failed", async () => {
    getInspection.mockResolvedValue(
      inspectionFixture({ can_release: false, suggested_outcome: "FAILED", results: [FAILED_CRITICAL] }),
    );
    renderScreen(<InspectionDetailPage />, [READ, APPROVE, REJECT]);
    const release = await screen.findByRole("button", { name: "Release" });
    expect((release as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("A critical parameter failed")).toBeDefined();
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText("9")).toBeDefined();
    expect(screen.getByText("Fail")).toBeDefined();
  });

  it("releases everything by sending no quantity at all", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(inspectionFixture());
    releaseInspection.mockResolvedValue(inspectionFixture({ status: "PASSED" }));
    renderScreen(<InspectionDetailPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Release" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Release all" }));

    await waitFor(() => expect(releaseInspection).toHaveBeenCalledTimes(1));
    const [id, body, key] = releaseInspection.mock.calls[0] as [string, Record<string, unknown>, string];
    expect(id).toBe("insp-1");
    expect(body).toEqual({});
    expect(key.length).toBeGreaterThan(10);
  });

  it("releases part as an exact decimal string, with the bucket for the remainder", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(inspectionFixture());
    releaseInspection.mockResolvedValue(inspectionFixture({ status: "PARTIAL_PASS" }));
    renderScreen(<InspectionDetailPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Release" }));
    const dialog = await screen.findByRole("dialog");
    const quantity = within(dialog).getByLabelText(/Quantity to release/);
    await user.clear(quantity);
    await user.type(quantity, "25.5");
    await user.selectOptions(within(dialog).getByLabelText("Send the remainder to"), "QUARANTINE");
    await user.click(within(dialog).getByRole("button", { name: "Release part" }));

    await waitFor(() => expect(releaseInspection).toHaveBeenCalledTimes(1));
    const body = releaseInspection.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body).toEqual({ accepted: "25.5", rejected_bucket: "QUARANTINE" });
    expect(typeof body.accepted).toBe("string");
  });

  it("refuses more than was inspected, and a non-number, without calling the server", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(inspectionFixture());
    renderScreen(<InspectionDetailPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Release" }));
    const dialog = await screen.findByRole("dialog");
    const quantity = within(dialog).getByLabelText(/Quantity to release/);
    await user.clear(quantity);
    await user.type(quantity, "41");
    await user.click(within(dialog).getByRole("button", { name: /Release/ }));
    expect(await within(dialog).findByText(/Only 40.125 m was inspected/)).toBeDefined();

    await user.clear(quantity);
    await user.type(quantity, "abc");
    await user.click(within(dialog).getByRole("button", { name: /Release/ }));
    expect(await within(dialog).findByText(/Enter a quantity above zero/)).toBeDefined();
    expect(releaseInspection).not.toHaveBeenCalled();
  });

  it("needs a note to release when any reading failed", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(
      inspectionFixture({
        results: [{ ...FAILED_CRITICAL, is_critical: false, param_name: "GSM", id: "r-2" }],
        suggested_outcome: "HOLD",
      }),
    );
    releaseInspection.mockResolvedValue(inspectionFixture({ status: "PASSED" }));
    renderScreen(<InspectionDetailPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Release" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Release all" }));
    expect(await within(dialog).findByText(/say why this stock is being released anyway/)).toBeDefined();
    expect(releaseInspection).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText(/Note/), "customer accepts");
    await user.click(within(dialog).getByRole("button", { name: "Release all" }));
    await waitFor(() => expect(releaseInspection).toHaveBeenCalledTimes(1));
    expect(releaseInspection.mock.calls[0]?.[1]).toEqual({ note: "customer accepts" });
  });

  it("needs a reason to reject, then sends it with the chosen bucket", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(inspectionFixture());
    rejectInspection.mockResolvedValue(inspectionFixture({ status: "FAILED" }));
    renderScreen(<InspectionDetailPage />, [READ, REJECT]);

    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reject stock" }));
    expect(await within(dialog).findByText(/Say why this stock is being rejected/)).toBeDefined();
    expect(rejectInspection).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText(/Reason/), "torn selvedge");
    await user.selectOptions(within(dialog).getByLabelText("Hold the rejected stock in"), "QUARANTINE");
    await user.click(within(dialog).getByRole("button", { name: "Reject stock" }));
    await waitFor(() => expect(rejectInspection).toHaveBeenCalledTimes(1));
    expect(rejectInspection.mock.calls[0]?.[1]).toEqual({ reason: "torn selvedge", bucket: "QUARANTINE" });
  });

  it("shows the server's sentence when a decision is refused, and leaves the dialog open", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(inspectionFixture());
    const { ApiError } = await import("@opsgrids/client");
    rejectInspection.mockRejectedValue(
      new ApiError(409, "Conflict", { title: "Conflict", status: 409, detail: "Inspection cannot move FAILED -> FAILED" }),
    );
    renderScreen(<InspectionDetailPage />, [READ, REJECT]);

    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Reason/), "bad");
    await user.click(within(dialog).getByRole("button", { name: "Reject stock" }));
    expect(await within(dialog).findByText(/cannot move FAILED -> FAILED/)).toBeDefined();
    expect(screen.getByRole("dialog")).toBeDefined();
  });

  it("records numeric readings as typed decimal strings and leaves unanswered rows out", async () => {
    const user = userEvent.setup();
    getInspection.mockResolvedValue(inspectionFixture({ template_id: "tpl-1" }));
    getQcTemplate.mockResolvedValue({
      id: "tpl-1",
      code: "INC-FAB",
      name: "Incoming fabric",
      kind: "incoming",
      item_id: null,
      description: "",
      is_active: true,
      created_at: "",
      updated_at: "",
      params: [
        { id: "p1", name: "GSM", kind: "numeric", unit: "g/m2", min: "170", max: "190", is_critical: false, position: 0 },
        { id: "p2", name: "Colour match", kind: "pass_fail", unit: "", min: null, max: null, is_critical: false, position: 1 },
      ],
    });
    recordInspectionResults.mockResolvedValue(inspectionFixture());
    renderScreen(<InspectionDetailPage />, [READ, INSPECT]);

    await user.click(await screen.findByRole("button", { name: "Record readings" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(await within(dialog).findByLabelText("GSM value"), "182.5");
    await user.click(within(dialog).getByRole("button", { name: "Save readings" }));

    await waitFor(() => expect(recordInspectionResults).toHaveBeenCalledTimes(1));
    expect(recordInspectionResults.mock.calls[0]).toEqual([
      "insp-1",
      [{ param_name: "GSM", value: "182.5", note: "" }],
    ]);
  });
});
