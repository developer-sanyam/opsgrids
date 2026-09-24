import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { EwayState } from "@/api/types";
import { EwayStateCell } from "./EwayStateCell";

const { retryMock } = vi.hoisted(() => ({ retryMock: vi.fn() }));

vi.mock("@/api/client", () => ({
  retryEwayBill: retryMock,
}));

const RETRY = ["finance.compliance.eway.retry:ORG"] as const;
const READ_ONLY = ["finance.compliance.eway.read:ORG"] as const;

function state(over: Partial<EwayState>): EwayState {
  return {
    required: true,
    status: null,
    eway_bill_no: null,
    valid_from: null,
    valid_until: null,
    terminal: false,
    cancellable_until: null,
    remedy: null,
    attempts: 0,
    remedy_detail: null,
    error_code: null,
    error: null,
    attempt: null,
    max_attempts: 8,
    retry_at: null,
    can_retry: false,
    ...over,
  };
}

function retrying(over: Partial<EwayState> = {}): EwayState {
  return state({
    status: "pending",
    attempts: 3,
    attempt: 3,
    error_code: "unavailable",
    error: "connection reset by peer",
    retry_at: new Date(Date.now() + 4 * 60_000).toISOString(),
    can_retry: true,
    ...over,
  });
}

function renderCell(s: EwayState, grants: readonly string[] = RETRY, onRetried = vi.fn()) {
  render(
    <PermissionProvider grants={grants}>
      <EwayStateCell invoiceId="inv-1" invoiceNumber="INV-2026-0160" state={s} onRetried={onRetried} />
    </PermissionProvider>,
  );
  return { onRetried };
}

beforeEach(() => {
  retryMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("a generation being retried", () => {
  it("says which attempt it is on, in words rather than colour", () => {
    renderCell(retrying());
    expect(screen.getByText("Retrying — attempt 3 of 8")).toBeTruthy();
    expect(screen.getByText(/cannot produce a second bill/i)).toBeTruthy();
  });

  it("counts down to the next attempt and keeps counting", () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    renderCell(retrying({ retry_at: new Date(Date.now() + 65_000).toISOString() }));

    expect(screen.getByRole("timer").textContent).toBe("Next attempt in 1m 05s");
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByRole("timer").textContent).toBe("Next attempt in 1m 00s");
  });

  it("says the retry is due rather than counting below zero", () => {
    renderCell(retrying({ retry_at: new Date(Date.now() - 30_000).toISOString() }));
    expect(screen.getByRole("timer").textContent).toBe("Next attempt due any moment now");
  });

  it("shows the portal's own reason, code first, as the server masked it", () => {
    renderCell(retrying({ error_code: "rate_limited", error: "too many requests" }));
    expect(screen.getByText(/rate_limited — too many requests/)).toBeTruthy();
    expect(screen.getByText(/asked to be left alone/i)).toBeTruthy();
  });

  it("uses a timer role, so the countdown is not announced every second", () => {
    renderCell(retrying());
    expect(screen.getByRole("timer").getAttribute("aria-live")).not.toBe("assertive");
  });
});

describe("a generation the platform gave up on", () => {
  it("says the portal never answered, and offers a retry to whoever may press it", () => {
    renderCell(
      state({
        status: "failed",
        terminal: true,
        attempt: 8,
        error_code: "retry_exhausted",
        remedy: "retry_now",
        remedy_detail: "The portal could not be reached in 8 attempts. If the goods are still to move, retry now.",
        can_retry: true,
      }),
    );
    expect(screen.getByText("Gave up — the portal never answered")).toBeTruthy();
    expect(screen.getByText(/retry now/i, { selector: "p" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry now…" })).toBeTruthy();
    expect(screen.queryByRole("timer")).toBeNull();
  });

  it("names a request that went stale", () => {
    renderCell(
      state({ status: "failed", terminal: true, error_code: "stale", remedy: "retry_now", can_retry: true }),
    );
    expect(screen.getByText("Not raised — the request went stale")).toBeTruthy();
  });
});

describe("a generation the portal refused", () => {
  const refused = state({
    status: "failed",
    terminal: true,
    attempt: 1,
    error_code: "113",
    error: "a road movement must name the vehicle carrying it",
    remedy: "correct_and_resubmit",
    remedy_detail: "A road movement must name the vehicle carrying it. Add the vehicle number (Part B) and raise the bill again.",
    can_retry: false,
  });

  it("carries the portal's code and says what to fix", () => {
    renderCell(refused);
    expect(screen.getByText("Refused by the portal (code 113)")).toBeTruthy();
    expect(screen.getByText(/Add the vehicle number \(Part B\)/)).toBeTruthy();
  });

  it("offers no retry — the identical consignment would be refused again — even to someone who may", () => {
    renderCell(refused);
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });
});

describe("who is offered the retry", () => {
  it("hides it from a caller who lacks the permission", () => {
    renderCell(retrying(), READ_ONLY);
    expect(screen.getByText("Retrying — attempt 3 of 8")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /retry now/i })).toBeNull();
  });

  it("hides it while permissions are unknown, rather than flashing it", () => {
    render(
      <EwayStateCell invoiceId="inv-1" invoiceNumber="INV-2026-0160" state={retrying()} onRetried={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: /retry now/i })).toBeNull();
  });

  it("does not offer it when the server says it would not be accepted", () => {
    renderCell(retrying({ can_retry: false }));
    expect(screen.queryByRole("button", { name: /retry now/i })).toBeNull();
  });
});

describe("retrying now", () => {
  it("asks first — in a dialog, never window.confirm — with focus on Cancel", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    renderCell(retrying());

    await user.click(screen.getByRole("button", { name: "Retry now…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toMatch(/INV-2026-0160/);
    expect(dialog.textContent).toMatch(/cannot produce a second bill/i);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(retryMock).not.toHaveBeenCalled();
  });

  it("cancelling makes no request", async () => {
    const user = userEvent.setup();
    renderCell(retrying());
    await user.click(screen.getByRole("button", { name: "Retry now…" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(retryMock).not.toHaveBeenCalled();
  });

  it("requests once, however many times it is clicked, and tells the page to look again", async () => {
    let resolve: (value: unknown) => void = () => undefined;
    retryMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    const { onRetried } = renderCell(retrying());

    await user.click(screen.getByRole("button", { name: "Retry now…" }));
    const confirm = await screen.findByRole("button", { name: "Retry now" });
    await user.click(confirm);
    await user.click(confirm);
    expect(retryMock).toHaveBeenCalledTimes(1);
    expect(retryMock).toHaveBeenCalledWith("inv-1");

    await act(async () => {
      resolve(retrying());
    });
    await waitFor(() => expect(onRetried).toHaveBeenCalledTimes(1));
    expect((await screen.findByRole("status")).textContent).toMatch(/Retry requested/);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // Asked once; not asked again until something newer happens.
    expect(screen.queryByRole("button", { name: /retry now/i })).toBeNull();
  });

  it("shows the server's sentence when it refuses, and keeps the dialog open to try again", async () => {
    retryMock.mockRejectedValue(
      new ApiError(409, "Conflict", {
        type: "https://errors.opsgrids.dev/conflict",
        title: "Conflict",
        status: 409,
        detail: "An e-Way generation for INV-2026-0160 is already in progress; wait for it to finish",
        request_id: "req-7",
      }),
    );
    const user = userEvent.setup();
    const { onRetried } = renderCell(retrying());

    await user.click(screen.getByRole("button", { name: "Retry now…" }));
    await user.click(await screen.findByRole("button", { name: "Retry now" }));

    expect(await screen.findByText(/already in progress/)).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onRetried).not.toHaveBeenCalled();
  });

  it("is reachable and operable by keyboard alone", async () => {
    retryMock.mockResolvedValue(retrying());
    const user = userEvent.setup();
    renderCell(retrying());

    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Retry now…" }));
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog")).toBeTruthy();
    // Focus lands on the safe choice, and Escape backs out without a request.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" })),
    );
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(retryMock).not.toHaveBeenCalled();

    // Reopened and confirmed from the keyboard.
    await user.click(screen.getByRole("button", { name: "Retry now…" }));
    const confirm = await screen.findByRole("button", { name: "Retry now" });
    confirm.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(retryMock).toHaveBeenCalledTimes(1));
  });
});

describe("nothing to say", () => {
  it("renders nothing for a bill that stands", () => {
    const { container } = render(
      <PermissionProvider grants={RETRY}>
        <EwayStateCell
          invoiceId="inv-1"
          invoiceNumber="INV-2026-0160"
          state={state({ status: "generated", eway_bill_no: "351009876543" })}
          onRetried={vi.fn()}
        />
      </PermissionProvider>,
    );
    expect(container.textContent).toBe("");
  });
});
