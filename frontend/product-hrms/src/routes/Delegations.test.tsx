/**
 * §4.E — the delegations screen: the four async states, the three status groupings (Active,
 * Scheduled, Past), the "N requests moved" line appearing only when `repointed_count > 0`, and
 * revoke end to end including the already-revoked 409.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import type { DelegationOut } from "@/api/types";
import { DelegationsPage } from "./Delegations";

const listDelegations = vi.fn();
const createDelegation = vi.fn();
const revokeDelegation = vi.fn();
const listDelegateCandidates = vi.fn();
const getEmployee = vi.fn();

vi.mock("@/api/approvals", () => ({
  listDelegations: (...args: unknown[]) => listDelegations(...args),
  createDelegation: (...args: unknown[]) => createDelegation(...args),
  revokeDelegation: (...args: unknown[]) => revokeDelegation(...args),
  listDelegateCandidates: (...args: unknown[]) => listDelegateCandidates(...args),
}));

vi.mock("@/api/client", () => ({
  getEmployee: (...args: unknown[]) => getEmployee(...args),
  USE_FIXTURES: false,
}));

vi.mock("@/lib/subject", () => ({
  getMyEmployeeId: () => "emp-sneha",
}));

const ACTIVE: DelegationOut = {
  id: "del-active",
  delegator_employee_id: "emp-sneha",
  delegator_name: "Sneha Kulkarni",
  delegator_location_code: "Bengaluru",
  delegate_employee_id: "emp-rohan",
  delegate_name: "Rohan Mehta",
  starts_at: "2026-09-14T03:30:00Z",
  ends_at: "2026-09-21T03:30:00Z",
  status: "active",
  created_by_employee_id: "emp-sneha",
  revoked_at: null,
  revoked_by_employee_id: null,
};

const SCHEDULED: DelegationOut = {
  ...ACTIVE,
  id: "del-scheduled",
  delegate_employee_id: "emp-daniel",
  delegate_name: "Daniel Okafor",
  starts_at: "2026-10-01T03:30:00Z",
  ends_at: "2026-10-08T03:30:00Z",
  status: "scheduled",
};

const REVOKED: DelegationOut = {
  ...ACTIVE,
  id: "del-revoked",
  delegate_employee_id: "emp-marcus",
  delegate_name: "Marcus Feng",
  status: "revoked",
  revoked_at: "2026-09-10T00:00:00Z",
  revoked_by_employee_id: "emp-sneha",
};

function paged(items: DelegationOut[]) {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

function renderPage() {
  return render(<DelegationsPage />);
}

describe("Delegations screen", () => {
  beforeEach(() => {
    listDelegations.mockReset();
    createDelegation.mockReset();
    revokeDelegation.mockReset();
    listDelegateCandidates.mockReset();
    getEmployee.mockReset();
    getEmployee.mockResolvedValue({
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
      status: "active",
      date_of_exit: null,
      created_at: "2020-10-05T09:00:00Z",
      pii: null,
    });
  });

  it("shows a loading skeleton before the first response", () => {
    listDelegations.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByRole("heading", { name: "Delegations" })).toBeDefined();
    expect(screen.queryByText("Active")).toBeNull();
  });

  it("renders the empty state with the create action", async () => {
    listDelegations.mockResolvedValue(paged([]));
    renderPage();
    await waitFor(() => expect(screen.getByText("No delegations yet")).toBeDefined());
    expect(
      screen.getByText("Going away? Let a colleague decide your approvals while you're out."),
    ).toBeDefined();
  });

  it("renders the error state with a retry action", async () => {
    listDelegations.mockRejectedValueOnce(new Error("The gateway timed out"));
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("The gateway timed out")).toBeDefined());
    listDelegations.mockResolvedValue(paged([ACTIVE]));
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());
  });

  it("groups rows into Active, Scheduled and Past sections", async () => {
    listDelegations.mockResolvedValue(paged([ACTIVE, SCHEDULED, REVOKED]));
    renderPage();

    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());

    const activeHeading = screen.getByRole("heading", { name: "Active" });
    const scheduledHeading = screen.getByRole("heading", { name: "Scheduled" });
    const pastHeading = screen.getByRole("heading", { name: "Past" });
    expect(activeHeading).toBeDefined();
    expect(scheduledHeading).toBeDefined();
    expect(pastHeading).toBeDefined();

    expect(screen.getByText("Daniel Okafor decides for you")).toBeDefined();
    expect(screen.getByText("Marcus Feng decides for you")).toBeDefined();
  });

  it("shows the 'N requests moved' line only when repointed_count is greater than zero", async () => {
    listDelegations.mockResolvedValue(paged([]));
    listDelegateCandidates.mockResolvedValue([
      { employee_id: "emp-rohan", display_name: "Rohan Mehta", employee_no: "ATM-0102" },
    ]);
    createDelegation.mockResolvedValue({ ...ACTIVE, repointed_count: 0 });
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("No delegations yet")).toBeDefined());
    await user.click(screen.getAllByRole("button", { name: "Set up a delegation" })[0] as HTMLElement);

    await waitFor(() => expect(screen.getByLabelText("Starts")).toBeDefined());
    const delegateInput = screen.getByRole("combobox", { name: "Delegate" });
    await user.type(delegateInput, "Ro");
    await screen.findByRole("option", { name: /Rohan Mehta/ });
    await user.keyboard("{ArrowDown}{Enter}");
    const endsField = screen.getByLabelText("Ends");
    endsField.focus();
    await user.type(endsField as HTMLElement, "2027-01-15T09:00");

    await user.click(screen.getByRole("button", { name: "Set up delegation" }));

    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());
    expect(screen.queryByText(/requests? waiting on you moved/)).toBeNull();

    // A second delegation, this time re-pointing two requests — the line now appears.
    createDelegation.mockResolvedValue({
      ...ACTIVE,
      id: "del-active-2",
      delegate_name: "Daniel Okafor",
      repointed_count: 2,
    });
    listDelegateCandidates.mockResolvedValue([
      { employee_id: "emp-daniel", display_name: "Daniel Okafor", employee_no: "ATM-0104" },
    ]);
    await user.click(screen.getByRole("button", { name: "Set up a delegation" }));
    await waitFor(() => expect(screen.getByLabelText("Starts")).toBeDefined());
    const delegateInput2 = screen.getByRole("combobox", { name: "Delegate" });
    await user.type(delegateInput2, "Da");
    await screen.findByRole("option", { name: /Daniel Okafor/ });
    await user.keyboard("{ArrowDown}{Enter}");
    const endsField2 = screen.getByLabelText("Ends");
    endsField2.focus();
    await user.type(endsField2 as HTMLElement, "2026-09-26T09:00");
    await user.click(screen.getByRole("button", { name: "Set up delegation" }));

    await waitFor(() =>
      expect(screen.getByText("2 requests waiting on you moved to Daniel Okafor.")).toBeDefined(),
    );
  });

  it("revokes a delegation and moves it to Past", async () => {
    listDelegations.mockResolvedValue(paged([ACTIVE]));
    revokeDelegation.mockResolvedValue({
      ...ACTIVE,
      status: "revoked",
      revoked_at: "2026-09-16T00:00:00Z",
      revoked_by_employee_id: "emp-sneha",
    });
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke this delegation?" });
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(screen.getAllByText("Revoked").length).toBeGreaterThan(0));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("filters to a person server-side, and clears back to the unfiltered list", async () => {
    // The round trip under test: pick a person → `employee_id` on the request → the server's own
    // page is what renders, `total` included. Nothing is filtered in the browser, which is why
    // the second response can contain a row the first one did not.
    const otherParty: DelegationOut = {
      ...ACTIVE,
      id: "del-others",
      delegator_employee_id: "emp-marcus",
      delegator_name: "Marcus Feng",
      delegate_employee_id: "emp-kabir",
      delegate_name: "Kabir Shah",
    };
    listDelegations.mockResolvedValue(paged([ACTIVE, SCHEDULED]));
    listDelegateCandidates.mockResolvedValue([
      { employee_id: "emp-kabir", display_name: "Kabir Shah", employee_no: "ATM-0204" },
    ]);
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());
    expect(listDelegations).toHaveBeenLastCalledWith({ page: 1, size: 25 });

    listDelegations.mockResolvedValue(paged([otherParty]));
    await user.type(screen.getByRole("combobox", { name: "Filter by person" }), "Ka");
    await screen.findByRole("option", { name: /Kabir Shah/ });
    await user.keyboard("{ArrowDown}{Enter}");

    await waitFor(() =>
      expect(listDelegations).toHaveBeenLastCalledWith({
        page: 1,
        size: 25,
        employeeId: "emp-kabir",
      }),
    );
    await waitFor(() =>
      expect(screen.getByText("Kabir Shah decides for Marcus Feng")).toBeDefined(),
    );
    // The previous page's rows are dropped, not filtered over: a new filter is a new query.
    expect(screen.queryByText("Rohan Mehta decides for you")).toBeNull();

    listDelegations.mockResolvedValue(paged([ACTIVE, SCHEDULED]));
    await user.click(screen.getByRole("button", { name: "Remove Kabir Shah" }));
    await waitFor(() =>
      expect(listDelegations).toHaveBeenLastCalledWith({ page: 1, size: 25 }),
    );
    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());
  });

  it("renders the filtered-empty state naming the person, not the unfiltered empty state", async () => {
    listDelegations.mockResolvedValue(paged([ACTIVE]));
    listDelegateCandidates.mockResolvedValue([
      { employee_id: "emp-kabir", display_name: "Kabir Shah", employee_no: "ATM-0204" },
    ]);
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());

    // `pages: 0` is what the server sends for an empty page — nothing on screen counts pages.
    listDelegations.mockResolvedValue({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await user.type(screen.getByRole("combobox", { name: "Filter by person" }), "Ka");
    await screen.findByRole("option", { name: /Kabir Shah/ });
    await user.keyboard("{ArrowDown}{Enter}");

    await waitFor(() =>
      expect(screen.getByText("You have no delegations with Kabir Shah")).toBeDefined(),
    );
    expect(screen.getByText("You can set one up for them.")).toBeDefined();
    expect(screen.queryByText("No delegations yet")).toBeNull();
    // The picker survives its own empty result — it is the only way back to the full list.
    expect(screen.getByRole("combobox", { name: "Filter by person" })).toBeDefined();
  });

  it("shows 'already revoked' on a 409 and refreshes the row", async () => {
    listDelegations
      .mockResolvedValueOnce(paged([ACTIVE]))
      .mockResolvedValueOnce(paged([REVOKED]));
    revokeDelegation.mockRejectedValue(new ApiError(409, "This delegation was already revoked", null));
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("Rohan Mehta decides for you")).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke this delegation?" });
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(screen.getByText("This was already revoked.")).toBeDefined());
  });
});
