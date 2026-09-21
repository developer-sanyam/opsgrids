import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ConnectionOut } from "@/api/types";
import { ConnectionWizard } from "./ConnectionWizard";

/**
 * Two things are pinned here, and they are the two the wizard exists for.
 *
 * The first is that the transport comes from the kind: `domain.transport_for` knows Google's and
 * Microsoft's submission endpoints and cannot know a custom one, so exactly one of the three
 * choices may ask for a host. A wizard that asked everyone would be asking two customers in three
 * to look up something the service already has.
 *
 * The second is that the credential does not come back. It leaves once, on the request, and after
 * that nothing on screen — no text, no input value, no last-four — carries it.
 */

/** Long and distinctive, so finding it anywhere in the DOM is a real finding and not a collision. */
const SECRET = "wqkz-hxtp-bnre-4f81";

const CONNECTED: ConnectionOut = {
  kind: "smtp",
  username: "engage@vaayu-logistics.example.com",
  host: "smtp.postmarkapp.com",
  port: 587,
  status: "connected",
  daily_cap: 2_000,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function mount() {
  const onConnected = vi.fn();
  render(<ConnectionWizard onConnected={onConnected} />);
  return { user: userEvent.setup(), onConnected };
}

/** Answer question one, land on question two. */
async function chooseKind(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await user.click(screen.getByRole("radio", { name }));
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

describe("choosing how the organization sends", () => {
  it("offers the three kinds the writer accepts, and not the one it refuses", () => {
    mount();
    expect(screen.getByRole("radio", { name: /Google Workspace/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Microsoft 365/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Custom SMTP/ })).toBeTruthy();
    // `platform` is in the wire enum and refused by `services.create_connection`. Offering it
    // would be a radio whose every outcome is that refusal.
    expect(screen.queryByRole("radio", { name: /OpsGrids-managed/ })).toBeNull();
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  it("asks for no host or port before the kind is chosen", () => {
    mount();
    expect(screen.queryByLabelText("Submission host")).toBeNull();
    expect(screen.queryByLabelText(/^Port/)).toBeNull();
  });

  it("reveals host and port for a custom SMTP server, which is the one kind we cannot derive", async () => {
    const { user } = mount();
    await chooseKind(user, /Custom SMTP/);

    expect(screen.getByLabelText("Submission host")).toBeTruthy();
    expect(screen.getByLabelText(/^Port/)).toBeTruthy();
    expect(screen.getByLabelText("SMTP username")).toBeTruthy();
  });

  it("asks Google Workspace for an account and a password, and nothing about a host", async () => {
    const { user } = mount();
    await chooseKind(user, /Google Workspace/);

    expect(screen.getByLabelText("Workspace account")).toBeTruthy();
    expect(screen.getByLabelText("App password")).toBeTruthy();
    expect(screen.queryByLabelText("Submission host")).toBeNull();
    expect(screen.queryByLabelText(/^Port/)).toBeNull();
  });

  it("asks Microsoft 365 for no host either", async () => {
    const { user } = mount();
    await chooseKind(user, /Microsoft 365/);

    expect(screen.getByLabelText("Microsoft 365 mailbox")).toBeTruthy();
    expect(screen.queryByLabelText("Submission host")).toBeNull();
    expect(screen.queryByLabelText(/^Port/)).toBeNull();
  });

  it("sends a host only for the kind that was asked for one", async () => {
    const spy = vi.spyOn(client, "connectProvider").mockResolvedValue(CONNECTED);
    const { user } = mount();

    await chooseKind(user, /Google Workspace/);
    await user.type(screen.getByLabelText("Workspace account"), "engage@vaayu.example.com");
    await user.type(screen.getByLabelText("App password"), SECRET);
    await user.click(screen.getByRole("button", { name: "Connect provider" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const sent = spy.mock.calls[0]?.[0];
    expect(sent?.kind).toBe("google");
    // Absent, not empty: an empty host means "derive it", and inventing one here would override
    // the service's own value with a guess.
    expect(sent === undefined ? true : "host" in sent).toBe(false);
    expect(sent === undefined ? true : "port" in sent).toBe(false);
  });
});

describe("the credential", () => {
  it("leaves once on the request and is nowhere on screen afterwards", async () => {
    const spy = vi.spyOn(client, "connectProvider").mockResolvedValue(CONNECTED);
    const { user, onConnected } = mount();

    await chooseKind(user, /Custom SMTP/);
    await user.type(screen.getByLabelText("SMTP username"), "engage@vaayu.example.com");
    await user.type(screen.getByLabelText("SMTP password"), SECRET);
    await user.type(screen.getByLabelText("Submission host"), "smtp.postmarkapp.com");
    await user.click(screen.getByRole("button", { name: "Connect provider" }));

    await waitFor(() => expect(onConnected).toHaveBeenCalledWith(CONNECTED));
    expect(spy.mock.calls[0]?.[0].secret).toBe(SECRET);

    // Not as text, not as markup, not as the value still sitting in the field.
    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain(SECRET);
    const values = Array.from(document.querySelectorAll("input")).map((input) => input.value);
    expect(values.some((value) => value.includes(SECRET))).toBe(false);
    // And no hint reconstructed from it: a last-four is the credential with a clue attached.
    expect(document.body.textContent).not.toContain(SECRET.slice(-4));
  });

  it("is not trimmed on the way out, because a leading space is part of a password", async () => {
    const spy = vi.spyOn(client, "connectProvider").mockResolvedValue(CONNECTED);
    const { user } = mount();

    await chooseKind(user, /Google Workspace/);
    await user.type(screen.getByLabelText("Workspace account"), "  engage@vaayu.example.com  ");
    await user.type(screen.getByLabelText("App password"), ` ${SECRET} `);
    await user.click(screen.getByRole("button", { name: "Connect provider" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[0].secret).toBe(` ${SECRET} `);
    // The username is ours to tidy: it is echoed back by the service and compared by eye.
    expect(spy.mock.calls[0]?.[0].username).toBe("engage@vaayu.example.com");
  });
});

describe("when the write fails", () => {
  it("shows the service's own sentence and keeps what was typed", async () => {
    vi.spyOn(client, "connectProvider").mockRejectedValue(
      new ApiError(
        409,
        "This organization already sends through notifications@vaayu.example.com. Disconnect that connection before adding another.",
        null,
      ),
    );
    const { user, onConnected } = mount();

    await chooseKind(user, /Custom SMTP/);
    await user.type(screen.getByLabelText("SMTP username"), "engage@vaayu.example.com");
    await user.type(screen.getByLabelText("SMTP password"), SECRET);
    await user.type(screen.getByLabelText("Submission host"), "smtp.postmarkapp.com");
    await user.click(screen.getByRole("button", { name: "Connect provider" }));

    expect(await screen.findByText(/already sends through/)).toBeTruthy();
    expect((screen.getByLabelText("SMTP username") as HTMLInputElement).value).toBe(
      "engage@vaayu.example.com",
    );
    expect((screen.getByLabelText("Submission host") as HTMLInputElement).value).toBe(
      "smtp.postmarkapp.com",
    );
    expect(onConnected).not.toHaveBeenCalled();
  });

  it("refuses to post a custom SMTP connection with no host, and says why", async () => {
    const spy = vi.spyOn(client, "connectProvider");
    const { user } = mount();

    await chooseKind(user, /Custom SMTP/);
    await user.type(screen.getByLabelText("SMTP username"), "engage@vaayu.example.com");
    await user.type(screen.getByLabelText("SMTP password"), SECRET);
    await user.click(screen.getByRole("button", { name: "Connect provider" }));

    expect(
      await screen.findByText("An SMTP connection needs the host its provider submits on."),
    ).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});
