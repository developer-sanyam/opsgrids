import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { ImsDocument } from "@/api/types";
import { ImsBulkBar } from "./ImsBulkBar";

const { bulkMock } = vi.hoisted(() => ({ bulkMock: vi.fn() }));

vi.mock("@/api/client", () => ({
  decideImsDocumentsBulk: bulkMock,
}));

const DECIDE = ["finance.compliance.ims.decide:ORG"] as const;
const READ_ONLY = ["finance.compliance.ims.read:ORG"] as const;

function doc(id: string, number: string): ImsDocument {
  return {
    id,
    source_ref: `ref-${id}`,
    source: "gstn",
    supplier_gstin: "27AAAAA0000A1Z5",
    supplier_name: "Vendor",
    document_number: number,
    document_type: "invoice",
    document_date: "2026-08-10",
    period: "2026-08",
    taxable_minor: 100_000,
    cgst_minor: 9_000,
    sgst_minor: 9_000,
    igst_minor: 0,
    total_minor: 118_000,
    irn: null,
    status: "pending",
    fetched_at: "2026-09-01T00:00:00Z",
  };
}

const DOCS = [doc("d-1", "INV-A"), doc("d-2", "INV-B")];

function renderBar(
  props: { reasons?: Record<string, string>; grants?: readonly string[] } = {},
) {
  const onClear = vi.fn();
  const onRecorded = vi.fn();
  render(
    <PermissionProvider grants={props.grants ?? DECIDE}>
      <ImsBulkBar
        selected={DOCS}
        reasons={props.reasons ?? {}}
        decidedBy="Priya Nair"
        onClear={onClear}
        onRecorded={onRecorded}
      />
    </PermissionProvider>,
  );
  return { onClear, onRecorded };
}

beforeEach(() => {
  bulkMock.mockReset();
  bulkMock.mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("who is offered the decisions", () => {
  it("offers the four to whoever may decide, and says how many are selected", () => {
    renderBar();
    expect(screen.getByText("2 documents selected")).toBeTruthy();
    for (const name of ["Accept…", "Reject…", "Hold…", "Take no action…"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
  });

  it("hides them from a caller who lacks the permission, and says what is missing", () => {
    renderBar({ grants: READ_ONLY });
    expect(screen.queryByRole("button", { name: "Accept…" })).toBeNull();
    expect(screen.getByText(/finance\.compliance\.ims\.decide/)).toBeTruthy();
    // Clearing a selection is not a decision, so it is always there.
    expect(screen.getByRole("button", { name: "Clear selection" })).toBeTruthy();
  });
});

describe("asking before recording", () => {
  it("confirms in a dialog, never window.confirm, saying what it does to the credit", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    renderBar();

    await user.click(screen.getByRole("button", { name: "Accept…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toMatch(/Accept 2 documents\?/);
    expect(dialog.textContent).toMatch(/Claims the input tax credit on each of them/);
    expect(dialog.textContent).toMatch(/its own reason/);
    expect(dialog.textContent).toMatch(/Nothing is sent to GSTN/);
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(bulkMock).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" })),
    );
  });

  it("makes no request when cancelled", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole("button", { name: "Accept…" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(bulkMock).not.toHaveBeenCalled();
  });

  it("is operable from the keyboard: open, back out with Escape, open and confirm", async () => {
    const user = userEvent.setup();
    const { onRecorded } = renderBar();

    screen.getByRole("button", { name: "Accept…" }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog")).toBeTruthy();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(bulkMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Accept…" }));
    const confirm = await screen.findByRole("button", { name: "Accept" });
    confirm.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(bulkMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onRecorded).toHaveBeenCalledWith({ count: 2, action: "accept" }));
  });
});

describe("recording", () => {
  it("sends one request with each row's own decision, and tells the page", async () => {
    const user = userEvent.setup();
    const { onRecorded } = renderBar({ reasons: { "d-1": "Duplicate of INV-9" } });

    await user.type(screen.getByLabelText(/Reason for every row without its own/), "Checked against the PO");
    await user.click(screen.getByRole("button", { name: "Reject…" }));
    await user.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => expect(bulkMock).toHaveBeenCalledTimes(1));
    expect(bulkMock).toHaveBeenCalledWith(
      [
        // The row's own reason wins over the shared one.
        { document_id: "d-1", action: "reject", note: "Duplicate of INV-9" },
        { document_id: "d-2", action: "reject", note: "Checked against the PO" },
      ],
      "Priya Nair",
    );
    await waitFor(() => expect(onRecorded).toHaveBeenCalledWith({ count: 2, action: "reject" }));
  });

  it("sends a null reason, not an empty string, for an accept nobody explained", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole("button", { name: "Accept…" }));
    await user.click(await screen.findByRole("button", { name: "Accept" }));

    await waitFor(() => expect(bulkMock).toHaveBeenCalledTimes(1));
    const [items] = bulkMock.mock.calls[0] as [{ note: string | null }[]];
    expect(items.map((item) => item.note)).toEqual([null, null]);
  });

  it("records once however many times the confirm is pressed", async () => {
    let resolve: (value: unknown) => void = () => undefined;
    bulkMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    renderBar();

    await user.click(screen.getByRole("button", { name: "Accept…" }));
    const confirm = await screen.findByRole("button", { name: "Accept" });
    await user.click(confirm);
    await user.click(confirm);
    expect(bulkMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve([]);
    });
  });
});

describe("reasons", () => {
  it("will not ask to reject without a reason for every row, and names the rows missing one", async () => {
    const user = userEvent.setup();
    renderBar({ reasons: { "d-1": "Duplicate" } });

    await user.click(screen.getByRole("button", { name: "Reject…" }));

    // No dialog at all: the check happens when the button is pressed, before asking.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/needs a reason for every document/)).toBeTruthy();
    expect(screen.getByText(/Still missing one for: INV-B\./)).toBeTruthy();
    expect(bulkMock).not.toHaveBeenCalled();
  });

  it("holds to the same rule for a hold, and lets an accept go without one", async () => {
    const user = userEvent.setup();
    renderBar();

    await user.click(screen.getByRole("button", { name: "Hold…" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/Hold needs a reason/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Accept…" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    // Moving on to a decision that needs no reason clears the earlier complaint.
    expect(screen.queryByText(/needs a reason for every document/)).toBeNull();
  });

  it("is satisfied by the shared reason, without typing one beside every row", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.type(screen.getByLabelText(/Reason for every row without its own/), "Not our purchase");
    await user.click(screen.getByRole("button", { name: "Reject…" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("does not take whitespace for a reason", async () => {
    const user = userEvent.setup();
    renderBar({ reasons: { "d-1": "   ", "d-2": "Duplicate" } });
    await user.click(screen.getByRole("button", { name: "Reject…" }));
    expect(screen.getByText(/Still missing one for: INV-A\./)).toBeTruthy();
  });
});

describe("when the server refuses", () => {
  it("says nothing was recorded when a document is no longer this organization's, and keeps the dialog", async () => {
    bulkMock.mockRejectedValue(
      new ApiError(404, "Not Found", {
        type: "about:blank",
        title: "Not Found",
        status: 404,
        detail: "document d-2 not found",
        request_id: "req-1",
      }),
    );
    const user = userEvent.setup();
    const { onRecorded } = renderBar();

    await user.click(screen.getByRole("button", { name: "Accept…" }));
    await user.click(await screen.findByRole("button", { name: "Accept" }));

    expect(await screen.findByText(/no longer part of this organization\. Nothing was recorded/)).toBeTruthy();
    expect(within(screen.getByRole("dialog")).getByText(/req-1/)).toBeTruthy();
    expect(onRecorded).not.toHaveBeenCalled();
  });

  it("shows a 403 in the server's own words rather than pretending it worked", async () => {
    bulkMock.mockRejectedValue(
      new ApiError(403, "Forbidden", {
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "missing permission finance.compliance.ims.decide",
        request_id: "req-2",
      }),
    );
    const user = userEvent.setup();
    const { onRecorded } = renderBar();

    await user.click(screen.getByRole("button", { name: "Accept…" }));
    await user.click(await screen.findByRole("button", { name: "Accept" }));

    expect(await screen.findByText(/missing permission/)).toBeTruthy();
    expect(onRecorded).not.toHaveBeenCalled();
  });
});

describe("clearing", () => {
  it("hands back to the page, which owns the selection", async () => {
    const user = userEvent.setup();
    const { onClear } = renderBar();
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
