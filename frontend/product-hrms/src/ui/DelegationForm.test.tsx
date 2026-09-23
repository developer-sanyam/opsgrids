/**
 * §3.3, §4.E — the delegation create form: the happy path, every refusal mapped to the control
 * that caused it, the picker's short-query and no-match states, and keyboard-only selection.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";
import { Announcer } from "@opsgrids/ui";

import type { EmployeeOut } from "@/api/types";
import type { FieldError } from "@/lib/problem";
import type { ZoneDirectory } from "@/lib/zone";
import { DelegationForm } from "./DelegationForm";

/** `ApiError`'s `problem` is `ProblemJson`, which does not declare `errors[]` (RFC 7807 leaves
 *  extension members to the API) — the same widening `@/lib/problem` itself documents. */
function problemWithErrors(detail: string, errors: FieldError[]): ProblemJson & { errors: FieldError[] } {
  return { detail, errors };
}

const createDelegation = vi.fn();
const listDelegateCandidates = vi.fn();
const getEmployee = vi.fn();

vi.mock("@/api/approvals", () => ({
  createDelegation: (...args: unknown[]) => createDelegation(...args),
  listDelegateCandidates: (...args: unknown[]) => listDelegateCandidates(...args),
}));

vi.mock("@/api/client", () => ({
  getEmployee: (...args: unknown[]) => getEmployee(...args),
}));

const ZONE_LOOKUP: ZoneDirectory = {
  locations: { Bengaluru: "Asia/Kolkata" },
  names: { Bengaluru: "Bengaluru" },
  organizationZone: "Asia/Kolkata",
};

const SNEHA: EmployeeOut = {
  id: "emp-sneha",
  membership_id: "mem-1",
  employee_no: "ATM-0301",
  full_name: "Sneha Kulkarni",
  designation: "HR Business Partner",
  job_title: "HR",
  department_id: null,
  location_code: "Bengaluru",
  employment_type: "full_time",
  work_mode: "office",
  date_of_joining: "2020-10-05",
  reporting_manager_id: null,
  dotted_line_manager_id: null,
  hierarchy_version: 1,
  status: "active",
  date_of_exit: null,
  created_at: "2020-10-05T09:00:00Z",
  pii: null,
};

const ROHAN_CANDIDATE = { employee_id: "emp-rohan", display_name: "Rohan Mehta", employee_no: "ATM-0102" };

function renderForm(props: Partial<Parameters<typeof DelegationForm>[0]> = {}) {
  const onCreated = vi.fn();
  const onCancel = vi.fn();
  render(
    <Announcer>
      <DelegationForm
        orgMode={false}
        myEmployeeId="emp-sneha"
        zoneLookup={ZONE_LOOKUP}
        onCreated={onCreated}
        onCancel={onCancel}
        {...props}
      />
    </Announcer>,
  );
  return { onCreated, onCancel };
}

async function waitForDateFields() {
  await waitFor(() => expect(screen.getByLabelText("Starts")).toBeDefined());
}

async function pickDelegateByKeyboard(name: RegExp | string) {
  const input = screen.getByRole("combobox", { name: "Delegate" });
  await userEvent.type(input, "Ro");
  await screen.findByRole("option", { name });
  await userEvent.keyboard("{ArrowDown}{Enter}");
}

async function fillEnds(value: string) {
  fireEvent.change(screen.getByLabelText("Ends"), { target: { value } });
}

/** The form refuses an end that is not in the future, and the fixtures below use fixed dates, so
 *  the clock is pinned before them. Only `Date` is faked: user-event still needs real timers. */
const NOW = new Date("2026-09-14T09:00:00+05:30");

describe("DelegationForm", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    createDelegation.mockReset();
    listDelegateCandidates.mockReset();
    getEmployee.mockReset();
    getEmployee.mockResolvedValue(SNEHA);
    listDelegateCandidates.mockResolvedValue([ROHAN_CANDIDATE]);
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  });

  it("creates a delegation for myself, with no delegator id sent", async () => {
    createDelegation.mockResolvedValue({
      id: "del-1",
      delegator_employee_id: "emp-sneha",
      delegator_name: "Sneha Kulkarni",
      delegator_location_code: "Bengaluru",
      delegate_employee_id: "emp-rohan",
      delegate_name: "Rohan Mehta",
      starts_at: "2026-09-15T03:30:00.000Z",
      ends_at: "2026-09-22T03:30:00.000Z",
      status: "active",
      created_by_employee_id: "emp-sneha",
      revoked_at: null,
      revoked_by_employee_id: null,
      repointed_count: 1,
    });
    const { onCreated } = renderForm();

    await waitForDateFields();
    await pickDelegateByKeyboard(/Rohan Mehta/);
    expect(screen.getByText("Rohan Mehta")).toBeDefined();

    await fillEnds("2026-09-22T09:00");
    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(createDelegation).toHaveBeenCalledTimes(1);
    const body = createDelegation.mock.calls[0]?.[0];
    expect(body.delegator_employee_id).toBeUndefined();
    expect(body.delegate_employee_id).toBe("emp-rohan");
    expect(body.ends_at).toBe(new Date("2026-09-22T09:00:00.000+05:30").toISOString());
  });

  it("sends delegator_employee_id only in ORG mode, for another employee", async () => {
    createDelegation.mockResolvedValue({
      id: "del-2",
      delegator_employee_id: "emp-priya",
      delegator_name: "Priya Nair",
      delegator_location_code: "Bengaluru",
      delegate_employee_id: "emp-rohan",
      delegate_name: "Rohan Mehta",
      starts_at: "2026-09-15T03:30:00.000Z",
      ends_at: "2026-09-22T03:30:00.000Z",
      status: "active",
      created_by_employee_id: "emp-sneha",
      revoked_at: null,
      revoked_by_employee_id: null,
      repointed_count: 0,
    });
    listDelegateCandidates.mockResolvedValue([
      { employee_id: "emp-priya", display_name: "Priya Nair", employee_no: "ATM-0103" },
    ]);
    renderForm({ orgMode: true });

    await waitForDateFields();

    const forInput = screen.getByRole("combobox", { name: "Set this up for" });
    await userEvent.type(forInput, "Pr");
    await screen.findByRole("option", { name: /Priya Nair/ });
    await userEvent.keyboard("{ArrowDown}{Enter}");

    listDelegateCandidates.mockResolvedValue([ROHAN_CANDIDATE]);
    await pickDelegateByKeyboard(/Rohan Mehta/);
    await fillEnds("2026-09-22T09:00");
    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    await waitFor(() => expect(createDelegation).toHaveBeenCalled());
    const body = createDelegation.mock.calls[0]?.[0];
    expect(body.delegator_employee_id).toBe("emp-priya");
  });

  it("requires a delegate before submitting", async () => {
    renderForm();
    await waitForDateFields();
    await fillEnds("2026-09-22T09:00");

    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    expect(screen.getByText("Choose who should decide for you.")).toBeDefined();
    expect(createDelegation).not.toHaveBeenCalled();
  });

  it("validates end-before-start on the client, without a round trip", async () => {
    renderForm();
    await waitForDateFields();
    await pickDelegateByKeyboard(/Rohan Mehta/);

    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "2026-09-22T09:00" } });
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "2026-09-20T09:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    expect(screen.getByText("End must be after the start.")).toBeDefined();
    expect(createDelegation).not.toHaveBeenCalled();
  });

  it("validates an end in the past on the client", async () => {
    renderForm();
    await waitForDateFields();
    await pickDelegateByKeyboard(/Rohan Mehta/);
    // Both in the past, but end still after start — isolates "in the past" from "before the
    // start", which the form (mirroring the server's own order) checks first.
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "2020-01-01T09:00" } });
    await fillEnds("2020-01-02T09:00");

    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    expect(screen.getByText("Pick a time in the future.")).toBeDefined();
    expect(createDelegation).not.toHaveBeenCalled();
  });

  it("maps a self-delegation 422 to the Delegate field", async () => {
    createDelegation.mockRejectedValue(
      new ApiError(
        422,
        "You can't delegate to yourself",
        problemWithErrors("You can't delegate to yourself", [
          {
            field: "delegate_employee_id",
            code: "self_delegation",
            message: "You can't delegate to yourself.",
          },
        ]),
      ),
    );
    renderForm();
    await waitForDateFields();
    await pickDelegateByKeyboard(/Rohan Mehta/);
    await fillEnds("2026-09-22T09:00");

    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    await waitFor(() => expect(screen.getByText("You can't delegate to yourself.")).toBeDefined());
  });

  it("maps a 409 overlap to an inline message naming the conflicting range", async () => {
    createDelegation.mockRejectedValue(
      new ApiError(
        409,
        "You already have a delegation that covers those dates.",
        { detail: "You already have a delegation from 2026-09-14T00:00:00Z to 2026-09-25T00:00:00Z that covers those dates." },
      ),
    );
    renderForm();
    await waitForDateFields();
    await pickDelegateByKeyboard(/Rohan Mehta/);
    await fillEnds("2026-09-22T09:00");

    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    await waitFor(() =>
      expect(
        screen.getByText(
          "You already have a delegation from 2026-09-14T00:00:00Z to 2026-09-25T00:00:00Z that covers those dates.",
        ),
      ).toBeDefined(),
    );
  });

  it("blocks submit while offline and keeps the form filled", async () => {
    renderForm();
    await waitForDateFields();
    await pickDelegateByKeyboard(/Rohan Mehta/);
    await fillEnds("2026-09-22T09:00");
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);

    await userEvent.click(screen.getByRole("button", { name: "Set up delegation" }));

    expect(screen.getByText("You're offline. Reconnect to save this.")).toBeDefined();
    expect(createDelegation).not.toHaveBeenCalled();
    expect(screen.getByText("Rohan Mehta")).toBeDefined();
  });

  describe("the delegate picker", () => {
    it("shows the short-query state below two characters, with no request sent", async () => {
      renderForm();
      await waitForDateFields();

      await userEvent.type(screen.getByRole("combobox", { name: "Delegate" }), "R");

      expect(screen.getByText("Keep typing — at least two letters.")).toBeDefined();
      expect(listDelegateCandidates).not.toHaveBeenCalled();
    });

    it("shows the no-matches state when the search comes back empty", async () => {
      listDelegateCandidates.mockResolvedValue([]);
      renderForm();
      await waitForDateFields();

      await userEvent.type(screen.getByRole("combobox", { name: "Delegate" }), "Zzz");

      await waitFor(() => expect(screen.getByText("No one matches “Zzz”.")).toBeDefined());
      expect(screen.getByText("Check the spelling, or try their employee number.")).toBeDefined();
    });

    it("is fully operable by keyboard — type, arrow to the option, Enter to select", async () => {
      renderForm();
      await waitForDateFields();

      await pickDelegateByKeyboard(/Rohan Mehta/);

      expect(screen.getByText("Rohan Mehta")).toBeDefined();
      expect(screen.getByLabelText(/Remove Rohan Mehta/)).toBeDefined();
    });
  });
});
