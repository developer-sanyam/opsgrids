import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ConnectionOut, Page } from "@/api/types";
import { ConnectionPage } from "./Connection";

/**
 * The screen, not the wizard: what is pinned here is that "Replace provider" reaches the same
 * wizard rather than a second form, that the confirmation is what stands between a click and the
 * `PUT`, and that a successful replace turns the panel over and points at Senders rather than
 * Domains — the two writes land on different next steps because a fresh connection has nothing
 * proven yet and a replaced one has everything to re-prove.
 */

const SECRET = "wqkz-hxtp-bnre-4f81";  // pragma: allowlist secret

const CONNECTED: ConnectionOut = {
  kind: "google",
  username: "notifications@vaayu-logistics.example.com",
  host: "smtp.gmail.com",
  port: 587,
  status: "connected",
  daily_cap: 2_000,
};

const REPLACED: ConnectionOut = {
  kind: "smtp",
  username: "billing@vaayu.example.com",
  host: "smtp.postmarkapp.com",
  port: 587,
  status: "connected",
  daily_cap: 5_000,
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function mount() {
  vi.spyOn(client, "getConnection").mockResolvedValue(page([CONNECTED]));
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <ConnectionPage />
    </MemoryRouter>,
  );
  await screen.findByText("Google Workspace");
  return { user };
}

/** Opens the replacement dialog and answers both questions with a different provider. */
async function openAndFillReplacement(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Replace provider" }));
  await user.click(screen.getByRole("radio", { name: /Custom SMTP/ }));
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.type(screen.getByLabelText("SMTP username"), "billing@vaayu.example.com");
  await user.type(screen.getByLabelText("SMTP password"), SECRET);
  await user.type(screen.getByLabelText("Submission host"), "smtp.postmarkapp.com");
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

/** The dialog's own "Replace provider" — distinct from the panel's, which opens the dialog and
 *  stays on screen behind it while the dialog is up. */
async function confirmReplace(user: ReturnType<typeof userEvent.setup>) {
  const dialog = screen.getByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Replace provider" }));
}

describe("the stale warning this screen used to show", () => {
  it("is gone — replacing is a real control now", async () => {
    await mount();
    expect(screen.queryByText(/isn't possible from here yet/)).toBeNull();
    expect(screen.queryByText(/which nothing in Engage can do today/)).toBeNull();
    expect(screen.getByRole("button", { name: "Replace provider" })).toBeTruthy();
  });
});

describe("replacing the provider", () => {
  it("opens the same wizard connecting uses, not a second form", async () => {
    const { user } = await mount();
    await user.click(screen.getByRole("button", { name: "Replace provider" }));

    expect(screen.getByRole("radio", { name: /Google Workspace/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Custom SMTP/ })).toBeTruthy();
  });

  it("states the sender-reset consequence before the PUT fires, and only fires it on confirm", async () => {
    const spy = vi.spyOn(client, "replaceProvider").mockResolvedValue(REPLACED);
    const { user } = await mount();

    await openAndFillReplacement(user);

    expect(
      screen.getAllByText(/resets every sender in this organization to unusable/).length,
    ).toBeGreaterThan(0);
    expect(spy).not.toHaveBeenCalled();

    await confirmReplace(user);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[0]).toEqual({
      kind: "smtp",
      username: "billing@vaayu.example.com",
      secret: SECRET,
      host: "smtp.postmarkapp.com",
    });
  });

  it("shows the new provider and points at Senders once the replace succeeds", async () => {
    vi.spyOn(client, "replaceProvider").mockResolvedValue(REPLACED);
    const { user } = await mount();

    await openAndFillReplacement(user);
    await confirmReplace(user);

    expect(await screen.findByText("Your own SMTP server")).toBeTruthy();
    expect(screen.getByText(/billing@vaayu\.example\.com/)).toBeTruthy();
    const senderLink = screen.getByRole("link", { name: "Review senders" });
    expect(senderLink.getAttribute("href")).toBe("/senders");
    expect(screen.getByText(/reset every sender to unusable/)).toBeTruthy();
    // The dialog itself is gone — the screen, not the modal, carries the news now.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the dialog open with the server's message when the replace is rejected", async () => {
    vi.spyOn(client, "replaceProvider").mockRejectedValue(
      new ApiError(422, "smtp.postmarkapp.com refused the credential: 535 5.7.8 auth failed", null),
    );
    const { user } = await mount();

    await openAndFillReplacement(user);
    await confirmReplace(user);

    expect(await screen.findByText(/535 5\.7\.8 auth failed/)).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    // The stale connection is still what the panel behind the dialog shows.
    expect(screen.getByText("Google Workspace")).toBeTruthy();
  });

  it("never renders the secret, before or after a successful replace", async () => {
    vi.spyOn(client, "replaceProvider").mockResolvedValue(REPLACED);
    const { user } = await mount();

    await openAndFillReplacement(user);
    expect(document.body.textContent).not.toContain(SECRET);

    await confirmReplace(user);
    await screen.findByText("Your own SMTP server");

    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(document.body.textContent).not.toContain(SECRET.slice(-4));
  });
});
