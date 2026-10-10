/**
 * QC templates (inventory plan Phase 4): managing them is `approve`, reading is `read`; bounds
 * leave the form as the typed decimal strings; a bad parameter is caught before any request.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { QcTemplateOut } from "@/api/types";
import { pageOf } from "@/test-utils/qualityFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { QcTemplatesPage } from "./QcTemplates";

const listQcTemplates = vi.fn();
const createQcTemplate = vi.fn();
const updateQcTemplate = vi.fn();
const deleteQcTemplate = vi.fn();

vi.mock("@/api/client", () => ({
  listQcTemplates: (...a: unknown[]) => listQcTemplates(...a),
  createQcTemplate: (...a: unknown[]) => createQcTemplate(...a),
  updateQcTemplate: (...a: unknown[]) => updateQcTemplate(...a),
  deleteQcTemplate: (...a: unknown[]) => deleteQcTemplate(...a),
}));

const READ = "operations.quality.inspection.read:ORG";
const APPROVE = "operations.quality.inspection.approve:ORG";

function template(over: Partial<QcTemplateOut> = {}): QcTemplateOut {
  return {
    id: "tpl-1",
    code: "INC-FAB",
    name: "Incoming fabric",
    kind: "incoming",
    item_id: null,
    description: "",
    is_active: true,
    params: [
      { id: "p1", name: "GSM", kind: "numeric", unit: "g/m2", min: "170", max: "190", is_critical: false, position: 0 },
      { id: "p2", name: "Shrinkage", kind: "numeric", unit: "%", min: null, max: "3.5", is_critical: true, position: 1 },
    ],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...over,
  };
}

describe("QcTemplatesPage", () => {
  beforeEach(() => {
    for (const fn of [listQcTemplates, createQcTemplate, updateQcTemplate, deleteQcTemplate]) fn.mockReset();
  });

  it("lists templates with their limits and criticality in words", async () => {
    listQcTemplates.mockResolvedValue(pageOf([template()]));
    renderScreen(<QcTemplatesPage />, [READ]);
    expect(await screen.findByText("Incoming fabric")).toBeDefined();
    expect(screen.getByText(/170 – 190 g\/m2/)).toBeDefined();
    expect(screen.getByText(/≤ 3\.5 % · critical/)).toBeDefined();
  });

  it("hides every management control without the approve permission", async () => {
    listQcTemplates.mockResolvedValue(pageOf([template()]));
    renderScreen(<QcTemplatesPage />, [READ]);
    await screen.findByText("Incoming fabric");
    expect(screen.queryByRole("button", { name: "New template" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Template INC-FAB/ })).toBeNull();
  });

  it("explains the empty state differently to someone who can create", async () => {
    listQcTemplates.mockResolvedValue(pageOf([]));
    const first = renderScreen(<QcTemplatesPage />, [READ]);
    expect(await screen.findByText("No QC templates yet")).toBeDefined();
    expect(screen.getByText(/A quality manager can create one/)).toBeDefined();
    first.unmount();

    listQcTemplates.mockResolvedValue(pageOf([]));
    renderScreen(<QcTemplatesPage />, [READ, APPROVE]);
    expect(await screen.findByText(/Create one to give inspections something to measure against/)).toBeDefined();
  });

  it("shows an error state that retries", async () => {
    const user = userEvent.setup();
    listQcTemplates.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(pageOf([template()]));
    renderScreen(<QcTemplatesPage />, [READ]);
    expect(await screen.findByText("Couldn't load QC templates")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again|retry/i }));
    expect(await screen.findByText("Incoming fabric")).toBeDefined();
  });

  it("creates a template, sending bounds as the typed decimal strings with an idempotency key", async () => {
    const user = userEvent.setup();
    listQcTemplates.mockResolvedValue(pageOf([]));
    createQcTemplate.mockResolvedValue(template());
    renderScreen(<QcTemplatesPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "New template" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Code"), "INC-FAB");
    await user.type(within(dialog).getByLabelText("Name"), "Incoming fabric");
    await user.type(within(dialog).getByLabelText("Parameter 1 name"), "GSM");
    await user.type(within(dialog).getByLabelText(/Parameter 1 minimum/), "170.5");
    await user.type(within(dialog).getByLabelText(/Parameter 1 maximum/), "190");
    await user.click(within(dialog).getByRole("button", { name: "Create template" }));

    await waitFor(() => expect(createQcTemplate).toHaveBeenCalledTimes(1));
    const [body, key] = createQcTemplate.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toMatchObject({
      code: "INC-FAB",
      name: "Incoming fabric",
      kind: "incoming",
      params: [{ name: "GSM", kind: "numeric", min: "170.5", max: "190", is_critical: false }],
    });
    expect(key.length).toBeGreaterThan(10);
  });

  it("catches a minimum above the maximum before any request", async () => {
    const user = userEvent.setup();
    listQcTemplates.mockResolvedValue(pageOf([]));
    renderScreen(<QcTemplatesPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "New template" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Code"), "X");
    await user.type(within(dialog).getByLabelText("Name"), "X");
    await user.type(within(dialog).getByLabelText("Parameter 1 name"), "GSM");
    await user.type(within(dialog).getByLabelText(/Parameter 1 minimum/), "0.3");
    await user.type(within(dialog).getByLabelText(/Parameter 1 maximum/), "0.1");
    await user.click(within(dialog).getByRole("button", { name: "Create template" }));

    expect(await within(dialog).findByText(/minimum is above the maximum/)).toBeDefined();
    expect(createQcTemplate).not.toHaveBeenCalled();
  });

  it("deletes after confirming", async () => {
    const user = userEvent.setup();
    listQcTemplates.mockResolvedValue(pageOf([template()]));
    deleteQcTemplate.mockResolvedValue(undefined);
    renderScreen(<QcTemplatesPage />, [READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: /Delete Template INC-FAB/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete template" }));
    await waitFor(() => expect(deleteQcTemplate).toHaveBeenCalledWith("tpl-1"));
  });
});
