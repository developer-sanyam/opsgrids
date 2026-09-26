import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as api from "@/api/automations";
import { AUTOMATION_ACTIVE, RUN_DETAIL_FAILED, STATS, page, RUN_FAILED, RUN_WAITING } from "@/api/automations.fixtures";
import { AutomationAnalyticsPage } from "./AutomationAnalytics";
import { RunDetailPage } from "./RunDetail";
import { RunList } from "./RunList";

/**
 * A run and its step timeline in each state it can be in, the retry that only a failed run
 * offers, and the run list and analytics screens' own loading, empty, error and populated states.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

const RUN_PATH = `/automations/${AUTOMATION_ACTIVE.id}/runs/${RUN_DETAIL_FAILED.id}`;

function renderRun() {
  return render(
    <MemoryRouter initialEntries={[RUN_PATH]}>
      <Routes>
        <Route path="/automations/:automationId/runs/:runId" element={<RunDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("run detail", () => {
  it("shows a loading status first", () => {
    vi.spyOn(api, "getRun").mockReturnValue(new Promise(() => {}));
    renderRun();
    expect(screen.getByRole("status").textContent).toBe("Loading run");
  });

  it("says what failed and retries the read", async () => {
    const get = vi.spyOn(api, "getRun").mockRejectedValueOnce(new ApiError(500, "The service is unavailable.", null));
    renderRun();
    expect(await screen.findByText("Couldn't load this run")).toBeTruthy();
    get.mockResolvedValue(RUN_DETAIL_FAILED);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Steps" })).toBeTruthy();
  });

  it("lays out the timeline, with the failed step's own reason and attempts", async () => {
    vi.spyOn(api, "getRun").mockResolvedValue(RUN_DETAIL_FAILED);
    renderRun();
    const steps = await screen.findByRole("list", { name: "Steps this run took" });
    const items = within(steps).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0] as HTMLElement).getByText("Done")).toBeTruthy();
    const failed = within(items[1] as HTMLElement);
    expect(failed.getByText("Failed")).toBeTruthy();
    expect(failed.getByText("Attempt 3")).toBeTruthy();
    expect(failed.getByText("Recipient has not opted in to WhatsApp.")).toBeTruthy();
    expect(screen.getByText("This run failed")).toBeTruthy();
    expect(screen.getByText("auto-1:evt-1001")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to automation" }).getAttribute("href")).toBe(`/automations/${AUTOMATION_ACTIVE.id}`);
  });

  it("retries a failed run only after confirming, and reads it again", async () => {
    const get = vi.spyOn(api, "getRun").mockResolvedValue(RUN_DETAIL_FAILED);
    const retry = vi.spyOn(api, "retryRun").mockResolvedValue({ ok: true });
    renderRun();
    await userEvent.click(await screen.findByRole("button", { name: "Retry run" }));
    expect(retry).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Retry run" }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith(RUN_DETAIL_FAILED.id));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it("keeps the dialog and shows the service's refusal when a retry is refused", async () => {
    vi.spyOn(api, "getRun").mockResolvedValue(RUN_DETAIL_FAILED);
    vi.spyOn(api, "retryRun").mockRejectedValue(new ApiError(409, "Only failed runs can be retried.", null));
    renderRun();
    await userEvent.click(await screen.findByRole("button", { name: "Retry run" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Retry run" }));
    expect(await within(dialog).findByText("Only failed runs can be retried.")).toBeTruthy();
  });

  it("offers no retry for a run that is waiting, and says when it wakes", async () => {
    vi.spyOn(api, "getRun").mockResolvedValue({
      ...RUN_WAITING,
      idempotency_key: "auto-1:evt-1002",
      steps: [
        { node_id: "wait_1", type: "wait", state: "waiting", started_at: "2026-09-25T09:00:00Z", finished_at: null, attempt: 1, detail: null, message_id: null },
      ],
    });
    renderRun();
    expect(await screen.findByText("Next wake-up")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
    expect(screen.queryByText("This run failed")).toBeNull();
  });

  it("says a run with no steps has not started", async () => {
    vi.spyOn(api, "getRun").mockResolvedValue({ ...RUN_DETAIL_FAILED, state: "running", failure_code: null, steps: [] });
    renderRun();
    expect(await screen.findByText("No steps yet")).toBeTruthy();
  });
});

describe("run list", () => {
  function renderList(published = true) {
    return render(
      <MemoryRouter>
        <RunList automationId={AUTOMATION_ACTIVE.id} published={published} />
      </MemoryRouter>,
    );
  }

  it("links each run to its detail and shows a failed run's reason", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue(page([RUN_FAILED, RUN_WAITING]));
    renderList();
    const row = (await screen.findByText("R*** S***")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Failed")).toBeTruthy();
    expect(within(row).getByText("recipient_not_opted_in")).toBeTruthy();
    expect(within(row).getByRole("link").getAttribute("href")).toBe(`/automations/${AUTOMATION_ACTIVE.id}/runs/${RUN_FAILED.id}`);
  });

  it("asks the service for one state", async () => {
    const list = vi.spyOn(api, "listRuns").mockResolvedValue(page([RUN_FAILED]));
    renderList();
    await screen.findByText("R*** S***");
    await userEvent.selectOptions(screen.getByLabelText("Filter runs by state"), "failed");
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(AUTOMATION_ACTIVE.id, expect.objectContaining({ state: "failed" })));
  });

  it("explains an empty list differently for a draft and a published automation", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue(page([]));
    const { unmount } = renderList(false);
    expect(await screen.findByText(/Publish this automation and its runs appear here/)).toBeTruthy();
    unmount();
    renderList(true);
    expect(await screen.findByText(/hasn't been triggered yet/)).toBeTruthy();
  });

  it("says what failed and retries", async () => {
    const list = vi.spyOn(api, "listRuns").mockRejectedValueOnce(new ApiError(500, "Timed out.", null));
    renderList();
    expect(await screen.findByText("Couldn't load the runs")).toBeTruthy();
    list.mockResolvedValue(page([RUN_FAILED]));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("R*** S***")).toBeTruthy();
  });
});

describe("analytics", () => {
  function renderAnalytics() {
    return render(
      <MemoryRouter>
        <AutomationAnalyticsPage />
      </MemoryRouter>,
    );
  }

  it("shows loading, then the totals, the failure reasons and per-automation rows", async () => {
    vi.spyOn(api, "getStats").mockResolvedValue(STATS);
    renderAnalytics();
    expect(screen.getByRole("status").textContent).toBe("Loading analytics");
    const totals = await screen.findByRole("region", { name: "Totals" });
    expect(within(totals).getByText("2,841")).toBeTruthy();
    expect(within(totals).getByText("4.2s")).toBeTruthy();
    expect(screen.getAllByText("Number not on WhatsApp").length).toBeGreaterThan(0);
    const row = screen.getByRole("link", { name: "Invoice Payment Reminder" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("84.3%")).toBeTruthy();
    expect(within(row).getByText("5.2s")).toBeTruthy();
  });

  it("has its own empty state, and a way back to the default range", async () => {
    vi.spyOn(api, "getStats").mockResolvedValue({ ...STATS, executions: 0, successful: 0, failed: 0, series: [], failures_by_reason: [], per_automation: [], avg_duration_ms: null });
    renderAnalytics();
    expect(await screen.findByText("No executions in this range")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reset to the last 30 days" })).toBeTruthy();
  });

  it("says what failed and retries", async () => {
    const stats = vi.spyOn(api, "getStats").mockRejectedValueOnce(new ApiError(500, "Timed out.", null));
    renderAnalytics();
    expect(await screen.findByText("Couldn't load the analytics")).toBeTruthy();
    stats.mockResolvedValue(STATS);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Totals" })).toBeTruthy();
  });

  it("asks the service for the range the person chose", async () => {
    const stats = vi.spyOn(api, "getStats").mockResolvedValue(STATS);
    renderAnalytics();
    await screen.findByRole("region", { name: "Totals" });
    const from = screen.getByLabelText("From");
    await userEvent.clear(from);
    await userEvent.type(from, "2026-09-01");
    await waitFor(() => expect(stats).toHaveBeenLastCalledWith(expect.objectContaining({ from: "2026-09-01" })));
  });
});
