import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { ImsPushSummary } from "@/api/types";
import { ImsPushPanel } from "./ImsPushPanel";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("@/api/client", () => ({
  requestImsPush: pushMock,
}));

const PUSH = ["finance.compliance.ims.push:ORG"] as const;
// Deciding is not sending: the two are separate permissions on purpose.
const DECIDE_ONLY = ["finance.compliance.ims.decide:ORG"] as const;

function summary(over: Partial<ImsPushSummary> = {}): ImsPushSummary {
  return {
    period: "2026-08",
    queued: 4,
    requested: 0,
    submitting: 0,
    pushed: 7,
    failed: 1,
    conflict: 0,
    drift: 0,
    pushable: 5,
    unqueued: 0,
    ...over,
  };
}

function renderPanel(
  props: { summary?: ImsPushSummary | null; period?: string; grants?: readonly string[] } = {},
) {
  const onRequested = vi.fn();
  render(
    <PermissionProvider grants={props.grants ?? PUSH}>
      <ImsPushPanel
        period={props.period ?? "2026-08"}
        summary={props.summary === undefined ? summary() : props.summary}
        onRequested={onRequested}
      />
    </PermissionProvider>,
  );
  return { onRequested };
}

beforeEach(() => {
  pushMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("where the period stands", () => {
  it("counts each state, labelled", () => {
    renderPanel({ summary: summary({ conflict: 2, drift: 3 }) });
    const counts = new Map(
      Array.from(document.querySelectorAll("dt")).map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]),
    );
    expect(counts.get("Waiting to send")).toBe("4");
    expect(counts.get("On GSTN")).toBe("7");
    expect(counts.get("Send failed")).toBe("1");
    expect(counts.get("Conflict")).toBe("2");
    expect(counts.get("GSTN differs")).toBe("3");
  });

  it("asks for a period rather than guessing which one is meant", () => {
    renderPanel({ period: "", summary: null });
    expect(screen.getByText(/Choose a return period above/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /send/i })).toBeNull();
  });

  it("says it is reading while the counts load", () => {
    renderPanel({ summary: null });
    expect(screen.getByText(/Reading where August 2026 stands/)).toBeTruthy();
  });

  it("explains what a conflict needs, because it is not the same as a failure", () => {
    renderPanel({ summary: summary({ conflict: 1 }) });
    expect(screen.getByText(/Decide it again to send a newer decision/i)).toBeTruthy();
  });
});

describe("who may send", () => {
  it("offers the send to whoever holds the push permission", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: "Send 5 decisions to GSTN…" })).toBeTruthy();
  });

  it("does not hand it to someone who can only decide, and says which permission it is", () => {
    renderPanel({ grants: DECIDE_ONLY });
    expect(screen.queryByRole("button", { name: /send/i })).toBeNull();
    expect(screen.getByText(/finance\.compliance\.ims\.push/)).toBeTruthy();
  });

  it("is disabled, and says so, when there is nothing to send", () => {
    renderPanel({ summary: summary({ pushable: 0 }) });
    const button = screen.getByRole("button", { name: "Nothing to send" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("uses the singular for one", () => {
    renderPanel({ summary: summary({ pushable: 1 }) });
    expect(screen.getByRole("button", { name: "Send 1 decision to GSTN…" })).toBeTruthy();
  });
});

describe("sending", () => {
  it("asks first, in a dialog, saying what it does and that a repeat is recorded once", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Send 5 decisions to GSTN…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toMatch(/Send 5 decisions for August 2026 to GSTN\?/);
    expect(dialog.textContent).toMatch(/recorded once/);
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" })),
    );
  });

  it("makes no request when cancelled", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Send 5 decisions to GSTN…" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("requests the period once, however many times it is confirmed, and reports how many", async () => {
    let resolve: (value: unknown) => void = () => undefined;
    pushMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    const { onRequested } = renderPanel();

    await user.click(screen.getByRole("button", { name: "Send 5 decisions to GSTN…" }));
    const confirm = await screen.findByRole("button", { name: "Send to GSTN" });
    await user.click(confirm);
    await user.click(confirm);
    expect(pushMock).toHaveBeenCalledTimes(1);
    expect(pushMock).toHaveBeenCalledWith("2026-08");

    await act(async () => {
      resolve({ period: "2026-08", requested: 5, not_pushable: 7 });
    });
    await waitFor(() => expect(onRequested).toHaveBeenCalledWith(5));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("says a push is under way, and that the page will update, while the worker has it", () => {
    renderPanel({ summary: summary({ requested: 3, submitting: 1 }) });
    const status = screen.getByRole("status");
    expect(status.textContent).toMatch(/Sending — 4 still under way/);
  });

  it("says nothing of a push when nothing is under way", () => {
    renderPanel();
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("when the server refuses", () => {
  it("answers a 429 with a wait, not a failure, and keeps the dialog to try again", async () => {
    pushMock.mockRejectedValue(
      new ApiError(429, "Too Many Requests", {
        type: "about:blank",
        title: "Too Many Requests",
        status: 429,
        detail: "rate limit",
        request_id: "req-9",
      }),
    );
    const user = userEvent.setup();
    const { onRequested } = renderPanel();

    await user.click(screen.getByRole("button", { name: "Send 5 decisions to GSTN…" }));
    await user.click(await screen.findByRole("button", { name: "Send to GSTN" }));

    expect(await screen.findByText(/asked for moments ago/)).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onRequested).not.toHaveBeenCalled();
  });

  it("shows a 403 in the server's words", async () => {
    pushMock.mockRejectedValue(
      new ApiError(403, "Forbidden", {
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "missing permission finance.compliance.ims.push",
        request_id: "req-3",
      }),
    );
    const user = userEvent.setup();
    const { onRequested } = renderPanel();

    await user.click(screen.getByRole("button", { name: "Send 5 decisions to GSTN…" }));
    await user.click(await screen.findByRole("button", { name: "Send to GSTN" }));

    expect(await screen.findByText(/missing permission/)).toBeTruthy();
    expect(onRequested).not.toHaveBeenCalled();
  });
});
