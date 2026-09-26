import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as api from "@/api/automations";
import {
  AUTOMATION_ACTIVE,
  AUTOMATION_DRAFT,
  AUTOMATION_PAUSED,
  TRIGGERS,
  page,
} from "@/api/automations.fixtures";
import { AutomationsPage } from "./Automations";

/**
 * The automation list: every state, filters that ask the service rather than filtering the page
 * received, and the three actions that each ask before they act.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

function renderList() {
  vi.spyOn(api, "listTriggers").mockResolvedValue(TRIGGERS);
  return render(
    <MemoryRouter initialEntries={["/automations"]}>
      <Routes>
        <Route path="/automations" element={<AutomationsPage />} />
        <Route path="/automations/new" element={<p>Builder page</p>} />
        <Route path="/automations/:id" element={<p>Open automation</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("states", () => {
  it("shows a status message while loading", () => {
    vi.spyOn(api, "listAutomations").mockReturnValue(new Promise(() => {}));
    renderList();
    expect(screen.getByRole("status", { name: "" }).textContent).toBe("Loading automations");
  });

  it("offers recipes and a way to create one when there are none", async () => {
    vi.spyOn(api, "listAutomations").mockResolvedValue(page([]));
    renderList();
    expect(await screen.findByText("No automations yet")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Start from a recipe" })).toBeTruthy();
    const link = screen.getByRole("link", { name: /Welcome a new lead/ });
    expect(link.getAttribute("href")).toBe("/automations/new?recipe=welcome-new-lead");
  });

  it("names the filter, and offers to clear it, when nothing matches", async () => {
    const list = vi.spyOn(api, "listAutomations").mockResolvedValueOnce(page([AUTOMATION_ACTIVE]));
    list.mockResolvedValueOnce(page([]));
    renderList();
    await screen.findByText("Invoice Payment Reminder");
    await userEvent.click(screen.getByRole("tab", { name: "Draft" }));
    expect(await screen.findByText("No automations match")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Start from a recipe" })).toBeNull();
    list.mockResolvedValue(page([AUTOMATION_ACTIVE]));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("Invoice Payment Reminder")).toBeTruthy();
  });

  it("says what failed and retries without a reload", async () => {
    const list = vi
      .spyOn(api, "listAutomations")
      .mockRejectedValueOnce(new ApiError(500, "The service is unavailable.", null));
    renderList();
    expect(await screen.findByText("Couldn't load your automations")).toBeTruthy();
    expect(screen.getByText("The service is unavailable.")).toBeTruthy();
    list.mockResolvedValue(page([AUTOMATION_ACTIVE]));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Invoice Payment Reminder")).toBeTruthy();
  });

  it("shows the trigger by its label, the rate as a percentage, and status in words", async () => {
    vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_ACTIVE, AUTOMATION_PAUSED, AUTOMATION_DRAFT]));
    renderList();
    const row = (await screen.findByRole("link", { name: "Invoice Payment Reminder" })).closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText("Invoice issued")).toBeTruthy();
    expect(cells.getByText("89.3%")).toBeTruthy();
    expect(cells.getByText("Active")).toBeTruthy();
    const paused = screen.getByRole("link", { name: "Ticket Acknowledgement" }).closest("tr");
    expect(within(paused as HTMLElement).getByText("Paused")).toBeTruthy();
    const draft = screen.getByRole("link", { name: "Welcome New Customer" }).closest("tr");
    expect(within(draft as HTMLElement).getByText("Draft")).toBeTruthy();
    expect(within(draft as HTMLElement).getByText("—")).toBeTruthy();
  });
});

describe("filters", () => {
  it("asks the service for one status, not the page it already has", async () => {
    const list = vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_ACTIVE]));
    renderList();
    await screen.findByText("Invoice Payment Reminder");
    await userEvent.click(screen.getByRole("tab", { name: "Paused" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ status: "paused" })));
  });

  it("filters by module using the modules the trigger catalogue names", async () => {
    const list = vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_ACTIVE]));
    renderList();
    await screen.findByText("Invoice Payment Reminder");
    await userEvent.selectOptions(await screen.findByLabelText("Module"), "CRM");
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ module: "CRM" })));
  });

  it("searches after typing pauses", async () => {
    const list = vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_ACTIVE]));
    renderList();
    await screen.findByText("Invoice Payment Reminder");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search automations" }), "invoice");
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ q: "invoice" })));
  });
});

async function openMenu(name: string) {
  await userEvent.click(await screen.findByRole("button", { name: `More actions for ${name}` }));
}

describe("actions", () => {
  it("pauses an active automation only after confirming", async () => {
    const list = vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_ACTIVE]));
    const pause = vi.spyOn(api, "pauseAutomation").mockResolvedValue({ ...AUTOMATION_ACTIVE, status: "paused" });
    renderList();
    await openMenu("Invoice Payment Reminder");
    await userEvent.click(screen.getByRole("menuitem", { name: "Pause" }));
    expect(pause).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Pause this automation?")).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Pause automation" }));
    await waitFor(() => expect(pause).toHaveBeenCalledWith(AUTOMATION_ACTIVE.id));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("offers Resume and Delete on a paused one, and Delete on a draft, but no Delete while active", async () => {
    vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_PAUSED, AUTOMATION_DRAFT, AUTOMATION_ACTIVE]));
    renderList();
    await openMenu("Ticket Acknowledgement");
    expect(screen.getByRole("menuitem", { name: "Resume" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    await openMenu("Welcome New Customer");
    expect(screen.getByRole("menuitem", { name: "Open builder" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Resume" })).toBeNull();
    await userEvent.keyboard("{Escape}");
    await openMenu("Invoice Payment Reminder");
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
  });

  it("keeps the dialog open and says why when the service refuses", async () => {
    vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_PAUSED]));
    const remove = vi
      .spyOn(api, "deleteAutomation")
      .mockRejectedValue(new ApiError(409, "Only draft or paused automations can be deleted.", null));
    renderList();
    await openMenu("Ticket Acknowledgement");
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete automation" }));
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    expect(await within(dialog).findByText("Only draft or paused automations can be deleted.")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
