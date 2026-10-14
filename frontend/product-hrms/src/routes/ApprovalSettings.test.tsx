/**
 * §4.G — the approval settings screen: the read-only rendering without `policy.write:ORG`, a
 * round trip through `PUT /policies/{request_type}`, a sub-floor value's field error, and the
 * escalation-off disclosure copy ADR-0006 §10 requires ("never auto-approves anything").
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError, PermissionProvider } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import type { PolicyOut } from "@/api/types";
import type { FieldError } from "@/lib/problem";
import { ApprovalSettingsPage } from "./ApprovalSettings";

const listPolicies = vi.fn();
const updatePolicy = vi.fn();

vi.mock("@/api/approvals", () => ({
  listPolicies: (...args: unknown[]) => listPolicies(...args),
  updatePolicy: (...args: unknown[]) => updatePolicy(...args),
}));

const REGULARIZATION_POLICY: PolicyOut = {
  request_type: "hrms.time.regularization",
  sla_minutes: 1440,
  enabled: true,
};

function renderReadOnly() {
  return render(<ApprovalSettingsPage />);
}

function renderEditable() {
  return render(
    <PermissionProvider grants={["core.approval.policy.write:ORG"]}>
      <ApprovalSettingsPage />
    </PermissionProvider>,
  );
}

describe("Approval settings", () => {
  beforeEach(() => {
    listPolicies.mockReset();
    updatePolicy.mockReset();
  });

  it("shows a loading skeleton before the first response", () => {
    listPolicies.mockReturnValue(new Promise(() => {}));
    renderReadOnly();
    expect(screen.getByRole("heading", { name: "Approval settings" })).toBeDefined();
    expect(screen.queryByText("Attendance correction")).toBeNull();
  });

  it("renders the error state with a retry action", async () => {
    listPolicies.mockRejectedValueOnce(new Error("The gateway timed out"));
    const user = userEvent.setup();
    renderReadOnly();

    await waitFor(() => expect(screen.getByText("The gateway timed out")).toBeDefined());
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("Attendance correction")).toBeDefined());
  });

  it("without policy.write:ORG, renders every value read-only behind the permission banner", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    renderReadOnly();

    await waitFor(() => expect(screen.getByText("Attendance correction")).toBeDefined());
    expect(screen.getByText("You can't change approval settings")).toBeDefined();
    expect(
      screen.getByText(
        "Only an approvals administrator can change these. You can still see the current settings below.",
      ),
    ).toBeDefined();
    expect(screen.getByText("1 day")).toBeDefined();
    expect(screen.getByText("Yes")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("round-trips a change to the SLA and announces the save", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    updatePolicy.mockResolvedValue({ ...REGULARIZATION_POLICY, sla_minutes: 2880 });
    const user = userEvent.setup();
    renderEditable();

    await waitFor(() => expect(screen.getByLabelText("Amount")).toBeDefined());
    expect(screen.getByLabelText("Amount")).toHaveProperty("value", "1");
    expect(screen.getByLabelText("Unit")).toHaveProperty("value", "days");

    await user.clear(screen.getByLabelText("Amount"));
    await user.type(screen.getByLabelText("Amount"), "2");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updatePolicy).toHaveBeenCalledWith("hrms.time.regularization", {
        sla_minutes: 2880,
        enabled: true,
      }),
    );
    await waitFor(() => expect(screen.getByText("Approval settings saved.")).toBeDefined());
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true);
  });

  it("says why Save is disabled until something changes", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    const user = userEvent.setup();
    renderEditable();

    await waitFor(() => expect(screen.getByLabelText("Amount")).toBeDefined());
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toHaveProperty("disabled", true);
    expect(screen.getByText("No changes to save.")).toBeDefined();
    expect(save.getAttribute("aria-describedby")).toBe(screen.getByText("No changes to save.").id);

    await user.type(screen.getByLabelText("Amount"), "2");
    expect(screen.queryByText("No changes to save.")).toBeNull();
  });

  it("puts the helper text under the amount and unit rather than squeezed beside them", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    renderEditable();

    await waitFor(() => expect(screen.getByLabelText("Amount")).toBeDefined());
    expect(screen.getByLabelText("Amount").getAttribute("inputmode")).toBe("decimal");
    expect(
      screen.getByText(/How long each approver gets before the request moves up to their manager/),
    ).toBeDefined();
  });

  it("a sub-floor value is caught before any request and names the 15-minute floor", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    const user = userEvent.setup();
    renderEditable();

    await waitFor(() => expect(screen.getByLabelText("Amount")).toBeDefined());
    await user.selectOptions(screen.getByLabelText("Unit"), "hours");
    await user.clear(screen.getByLabelText("Amount"));
    await user.type(screen.getByLabelText("Amount"), "0.1");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Give approvers at least 15 minutes.")).toBeDefined();
    expect(updatePolicy).not.toHaveBeenCalled();
  });

  it("surfaces the server's own field error, verbatim, on the sla_minutes control", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    const problem: ProblemJson & { errors: FieldError[] } = {
      detail: "Request payload failed validation",
      errors: [
        {
          field: "sla_minutes",
          code: "greater_than_equal",
          message: "Input should be greater than or equal to 15",
        },
      ],
    };
    updatePolicy.mockRejectedValue(
      new ApiError(422, "Request payload failed validation", problem),
    );
    const user = userEvent.setup();
    renderEditable();

    await waitFor(() => expect(screen.getByLabelText("Amount")).toBeDefined());
    await user.selectOptions(screen.getByLabelText("Unit"), "hours");
    await user.clear(screen.getByLabelText("Amount"));
    await user.type(screen.getByLabelText("Amount"), "1");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(
      await screen.findByText("Input should be greater than or equal to 15"),
    ).toBeDefined();
    // Never the client's own friendlier mirror in place of the server's real message (§2.5).
    expect(screen.queryByText("Give approvers at least 15 minutes.")).toBeNull();
  });

  it("names the honest consequence of turning escalation off, before saving", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    const user = userEvent.setup();
    renderEditable();

    await waitFor(() => expect(screen.getByRole("switch")).toBeDefined());
    expect(
      screen.getByText(
        "If an approver doesn't answer in time, the request moves up to their manager. If there's nobody above, it goes to HR.",
      ),
    ).toBeDefined();

    await user.click(screen.getByRole("switch"));

    expect(
      screen.getByText(
        "Turn escalation off? Requests will wait for their approver with no time limit.",
      ),
    ).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Turn it off" }));

    expect(
      screen.getByText(
        "Requests stay with the approver until they answer. They never approve themselves, and nothing is decided automatically — a request with escalation off can wait indefinitely.",
      ),
    ).toBeDefined();
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(updatePolicy).toHaveBeenCalledWith("hrms.time.regularization", {
        sla_minutes: 1440,
        enabled: false,
      }),
    );
  });

  it("'Keep escalation on' reverts the switch without saving anything", async () => {
    listPolicies.mockResolvedValue([REGULARIZATION_POLICY]);
    const user = userEvent.setup();
    renderEditable();

    await waitFor(() => expect(screen.getByRole("switch")).toBeDefined());
    await user.click(screen.getByRole("switch"));
    await user.click(screen.getByRole("button", { name: "Keep escalation on" }));

    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true);
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });
});
