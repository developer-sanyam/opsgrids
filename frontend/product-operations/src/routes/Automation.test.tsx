/**
 * Operational alerts and insight dashboards (fulfilment plan §4; F12). Covers the empty state,
 * a populated alert list, the dismiss action gated on `operations.automation.alert.manage`, and
 * the insight cards gated on `operations.automation.insight.read`.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { OperationalAlertOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { AutomationPage } from "./Automation";

const listAlerts = vi.fn();
const resolveAlert = vi.fn();
const insightBacklog = vi.fn();
const insightBottlenecks = vi.fn();
const insightWorkload = vi.fn();
const insightDiscrepancyTrend = vi.fn();

vi.mock("@/api/client", () => ({
  listAlerts: (...args: unknown[]) => listAlerts(...args),
  resolveAlert: (...args: unknown[]) => resolveAlert(...args),
  insightBacklog: (...args: unknown[]) => insightBacklog(...args),
  insightBottlenecks: (...args: unknown[]) => insightBottlenecks(...args),
  insightWorkload: (...args: unknown[]) => insightWorkload(...args),
  insightDiscrepancyTrend: (...args: unknown[]) => insightDiscrepancyTrend(...args),
}));

const READ_GRANTS = ["operations.automation.alert.read:ORG", "operations.automation.insight.read:ORG"];
const MANAGE_GRANTS = [...READ_GRANTS, "operations.automation.alert.manage:ORG"];

const LOW_STOCK_ALERT: OperationalAlertOut = {
  id: "alert-1",
  kind: "low_stock",
  subject_type: "inventory_item",
  subject_id: "item-1",
  warehouse_id: null,
  detail: { sku: "WGT-001", name: "Widget", on_hand: 2, reorder_point: 10 },
  detected_at: "2026-09-29T00:00:00Z",
  last_seen_at: "2026-09-29T00:00:00Z",
  resolved_at: null,
  resolved_by: null,
};

function emptyPage() {
  return { items: [], total: 0, page: 1, size: 20, pages: 0 };
}

beforeEach(() => {
  listAlerts.mockReset();
  resolveAlert.mockReset();
  insightBacklog.mockReset();
  insightBottlenecks.mockReset();
  insightWorkload.mockReset();
  insightDiscrepancyTrend.mockReset();

  listAlerts.mockResolvedValue(emptyPage());
  insightBacklog.mockResolvedValue([]);
  insightBottlenecks.mockResolvedValue([]);
  insightWorkload.mockResolvedValue([]);
  insightDiscrepancyTrend.mockResolvedValue([]);
});

describe("AutomationPage", () => {
  it("shows the empty state when nothing needs attention", async () => {
    renderScreen(<AutomationPage />, READ_GRANTS);
    expect(await screen.findByText("Nothing needs attention")).toBeDefined();
  });

  it("lists an open alert with its kind and summary", async () => {
    listAlerts.mockResolvedValue({ items: [LOW_STOCK_ALERT], total: 1, page: 1, size: 20, pages: 1 });
    renderScreen(<AutomationPage />, READ_GRANTS);

    expect(await screen.findByText("Low stock")).toBeDefined();
    expect(screen.getByText(/Widget — 2 on hand, reorder at 10/)).toBeDefined();
    expect(screen.getByText("Open")).toBeDefined();
  });

  it("hides the dismiss action without alert.manage, and dismisses with it", async () => {
    listAlerts.mockResolvedValue({ items: [LOW_STOCK_ALERT], total: 1, page: 1, size: 20, pages: 1 });
    const { unmount } = renderScreen(<AutomationPage />, READ_GRANTS);
    await screen.findByText("Low stock");
    expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
    unmount();

    resolveAlert.mockResolvedValue({ ...LOW_STOCK_ALERT, resolved_at: "2026-09-29T01:00:00Z", resolved_by: "user:asha" });
    const user = userEvent.setup();
    renderScreen(<AutomationPage />, MANAGE_GRANTS);
    await screen.findByText("Low stock");
    await user.click(screen.getByRole("button", { name: /dismiss low stock/i }));

    await waitFor(() => expect(resolveAlert).toHaveBeenCalledWith("alert-1"));
  });

  it("renders the insight cards from real query results", async () => {
    insightBacklog.mockResolvedValue([{ warehouse_id: "wh-1", open_tasks: 3, avg_age_minutes: 45, max_age_minutes: 90 }]);
    renderScreen(<AutomationPage />, READ_GRANTS);

    expect(await screen.findByText("Task backlog, by warehouse")).toBeDefined();
    expect(await screen.findByText("wh-1")).toBeDefined();
    expect(screen.getByText("3 open")).toBeDefined();
  });
});
