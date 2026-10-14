import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ProjectOut } from "@/api/types";
import { ProjectFormDialog } from "./ProjectFormDialog";

const CREATED: ProjectOut = {
  id: "proj-1",
  code: "PRJ-200",
  name: "New Engagement",
  client: "",
  owner: { id: "", name: "Unassigned", role: "" },
  phase: "discovery",
  health: "on_track",
  progress_pct: 0,
  start_date: "2026-01-01",
  target_date: "2026-06-01",
  budget_minor: 2400000,
  spent_minor: 0,
  currency: "INR",
  team_size: 0,
  open_tasks: 0,
  milestone_count: 0,
  summary: "",
};

afterEach(() => {
  vi.restoreAllMocks();
});

function openCreate(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<ProjectFormDialog project={null} members={[]} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Code"), "PRJ-200");
  await user.type(screen.getByLabelText("Name"), "New Engagement");
  await user.type(screen.getByLabelText("Start date"), "2026-01-01");
}

describe("ProjectFormDialog", () => {
  it("refuses a target date before the start date, client-side, before any request", async () => {
    const spy = vi.spyOn(client, "createProject");
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2025-12-01");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("The target date can't be before the start date.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("refuses a malformed budget without touching a float", async () => {
    const spy = vi.spyOn(client, "createProject");
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2026-06-01");
    const budget = screen.getByLabelText(/Budget/);
    await user.clear(budget);
    await user.type(budget, "24,000.999");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(
      await screen.findByText(
        "Enter an amount in digits, like 24000 or 24000.50, with no currency sign or words and no more than two decimal places.",
      ),
    ).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables submit and blocks a double submit while pending", async () => {
    let resolveCreate: (value: ProjectOut) => void = () => undefined;
    vi.spyOn(client, "createProject").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2026-06-01");
    const submit = screen.getByRole("button", { name: "Create project" });
    await user.click(submit);
    await user.click(submit);

    expect(screen.getByRole("button", { name: "Creating…" })).toBeTruthy();
    expect(client.createProject).toHaveBeenCalledTimes(1);
    resolveCreate(CREATED);
  });

  it("parses a major-unit amount into integer minor units, converts on submit, closes and reports", async () => {
    vi.spyOn(client, "createProject").mockResolvedValue(CREATED);
    const { user, onSaved, onClose } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2026-06-01");
    await user.type(screen.getByLabelText(/Budget/), "24000");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() =>
      expect(client.createProject).toHaveBeenCalledWith(
        expect.objectContaining({ code: "PRJ-200", budget_minor: 2400000 }),
      ),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED, "New Engagement created."));
    expect(onClose).toHaveBeenCalled();
  });

  it("a duplicate code (409) shows the server's detail without a field match, and keeps the input", async () => {
    const problem = { status: 409, title: "Conflict", detail: "That code is already in use." };
    vi.spyOn(client, "createProject").mockRejectedValue(new ApiError(409, "Conflict", problem));
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2026-06-01");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("That code is already in use.")).toBeTruthy();
    expect(screen.getByLabelText("Code")).toHaveProperty("value", "PRJ-200");
  });

  it("does not show the code field when editing — code is immutable", () => {
    render(<ProjectFormDialog project={CREATED} members={[]} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.queryByPlaceholderText("PRJ-104")).toBeNull();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();
  });
});

describe("ProjectFormDialog: money in each currency's own decimals", () => {
  function openEdit(existing: ProjectOut) {
    vi.spyOn(client, "updateProject").mockResolvedValue(existing);
    render(<ProjectFormDialog project={existing} members={[]} onClose={vi.fn()} onSaved={vi.fn()} />);
    return userEvent.setup();
  }

  it.each([
    ["INR", 250_000_000, 112_000_050, "2500000", "1120000.50"],
    ["JPY", 1_500_000, 250_000, "1500000", "250000"],
    ["KWD", 12_345, 5, "12.345", "0.005"],
  ])("shows a %s project's budget and spend as %s and %s", (currency, budget, spent, budgetShown, spentShown) => {
    openEdit({ ...CREATED, currency, budget_minor: budget, spent_minor: spent });
    expect(screen.getByLabelText(/^Budget/)).toHaveProperty("value", budgetShown);
    expect(screen.getByLabelText(/^Spent/)).toHaveProperty("value", spentShown);
  });

  it("saves a yen project untouched with its budget and spend exactly as stored", async () => {
    const user = openEdit({ ...CREATED, currency: "JPY", budget_minor: 1_500_000, spent_minor: 250_000 });
    await user.type(screen.getByLabelText("Name"), " II");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(client.updateProject).toHaveBeenCalledTimes(1));
    expect(client.updateProject).toHaveBeenCalledWith(
      "PRJ-200",
      expect.objectContaining({ budget_minor: 1_500_000, spent_minor: 250_000, currency: "JPY" }),
    );
  });

  it("saves a dinar project untouched with its budget exactly as stored", async () => {
    const user = openEdit({ ...CREATED, currency: "KWD", budget_minor: 12_345, spent_minor: 0 });
    await user.type(screen.getByLabelText("Name"), " II");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(client.updateProject).toHaveBeenCalledTimes(1));
    expect(client.updateProject).toHaveBeenCalledWith(
      "PRJ-200",
      expect.objectContaining({ budget_minor: 12_345, spent_minor: 0, currency: "KWD" }),
    );
  });

  it.each([
    ["24000.50", "INR", 2_400_050],
    ["1500000", "JPY", 1_500_000],
    ["12.345", "KWD", 12_345],
  ])("sends a budget of %s %s as %i minor units", async (typed, currency, minor) => {
    vi.spyOn(client, "createProject").mockResolvedValue(CREATED);
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2026-06-01");
    const code = screen.getByLabelText("Currency");
    await user.clear(code);
    await user.type(code, currency);
    await user.type(screen.getByLabelText(/^Budget/), typed);
    await user.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(client.createProject).toHaveBeenCalledTimes(1));
    expect(client.createProject).toHaveBeenCalledWith(
      expect.objectContaining({ budget_minor: minor, currency }),
    );
  });

  it("refuses a decimal budget for yen, before any request", async () => {
    const spy = vi.spyOn(client, "createProject");
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText("Target date"), "2026-06-01");
    const code = screen.getByLabelText("Currency");
    await user.clear(code);
    await user.type(code, "JPY");
    const budget = screen.getByLabelText(/^Budget/);
    await user.clear(budget);
    await user.type(budget, "1500.5");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(
      await screen.findByText("Enter a whole amount in digits, like 24000. JPY has no decimal places."),
    ).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});
