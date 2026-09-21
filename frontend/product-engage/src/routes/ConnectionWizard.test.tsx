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
const SECRET = "wqkz-hxtp-bnre-4f81";  // pragma: allowlist secret

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

/**
 * `mode="replace"` — the same two questions, opened in a dialog over a connection that already
 * exists, with a third step this wizard does not have in `"connect"` mode: a confirmation naming
 * what `replace_connection` actually does before the `PUT` that triggers it ever leaves the
 * browser.
 */
const REPLACED: ConnectionOut = {
  kind: "google",
  username: "billing@vaayu-logistics.example.com",
  host: "smtp.gmail.com",
  port: 587,
  status: "connected",
  daily_cap: 2_000,
};

function mountReplace() {
  const onConnected = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConnectionWizard mode="replace" open onConnected={onConnected} onCancel={onCancel} />,
  );
  return { user: userEvent.setup(), onConnected, onCancel };
}

/** Answers both questions and lands on the confirmation step — one call short of the `PUT`. */
async function reachConfirmStep(user: ReturnType<typeof userEvent.setup>) {
  await chooseKind(user, /Custom SMTP/);
  await user.type(screen.getByLabelText("SMTP username"), "billing@vaayu.example.com");
  await user.type(screen.getByLabelText("SMTP password"), SECRET);
  await user.type(screen.getByLabelText("Submission host"), "smtp.postmarkapp.com");
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

describe("replacing a connection", () => {
  it("collects the same two questions connecting does, not a second form", async () => {
    const { user } = mountReplace();
    expect(screen.getByRole("radio", { name: /Google Workspace/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Custom SMTP/ })).toBeTruthy();

    await chooseKind(user, /Custom SMTP/);
    expect(screen.getByLabelText("SMTP username")).toBeTruthy();
    expect(screen.getByLabelText("SMTP password")).toBeTruthy();
    expect(screen.getByLabelText("Submission host")).toBeTruthy();
  });

  it("does not call the PUT when the fields are answered but not yet confirmed", async () => {
    const spy = vi.spyOn(client, "replaceProvider");
    const { user } = mountReplace();

    await reachConfirmStep(user);

    expect(spy).not.toHaveBeenCalled();
  });

  it("names the sender-reset consequence on the confirmation step, not a generic 'are you sure?'", async () => {
    const { user } = mountReplace();
    await reachConfirmStep(user);

    // Said twice on this step — once as the dialog's own description, once in the alert
    // beneath it — so any one instance is enough to prove it is there and not buried.
    expect(
      screen.getAllByText(/resets every sender in this organization to unusable/).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/needs a new test message before it can send again/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/are you sure/i)).toBeNull();
  });

  it("issues the PUT with the same body shape connecting sends, only once confirmed", async () => {
    const putSpy = vi.spyOn(client, "replaceProvider").mockResolvedValue(REPLACED);
    const postSpy = vi.spyOn(client, "connectProvider");
    const { user, onConnected } = mountReplace();

    await reachConfirmStep(user);
    expect(putSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Replace provider" }));

    await waitFor(() => expect(putSpy).toHaveBeenCalled());
    const sent = putSpy.mock.calls[0]?.[0];
    expect(sent).toEqual({
      kind: "smtp",
      username: "billing@vaayu.example.com",
      secret: SECRET,
      host: "smtp.postmarkapp.com",
    });
    expect(onConnected).toHaveBeenCalledWith(REPLACED);
    // `connectProvider` is a different route entirely — a replace must never fall through to it.
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("keeps the dialog open and shows the server's own message when the replace is rejected, with the fields intact", async () => {
    vi.spyOn(client, "replaceProvider").mockRejectedValue(
      new ApiError(422, "smtp.postmarkapp.com refused the credential: 535 5.7.8 auth failed", null),
    );
    const { user, onConnected } = mountReplace();

    await reachConfirmStep(user);
    await user.click(screen.getByRole("button", { name: "Replace provider" }));

    expect(await screen.findByText(/535 5\.7\.8 auth failed/)).toBeTruthy();
    // Still up: the heading for the confirmation step is still on screen.
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Confirm the replacement" })).toBeTruthy();
    expect(onConnected).not.toHaveBeenCalled();

    // Stepping back confirms nothing was cleared by the refusal.
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect((screen.getByLabelText("SMTP username") as HTMLInputElement).value).toBe(
      "billing@vaayu.example.com",
    );
    expect((screen.getByLabelText("SMTP password") as HTMLInputElement).value).toBe(SECRET);
    expect((screen.getByLabelText("Submission host") as HTMLInputElement).value).toBe(
      "smtp.postmarkapp.com",
    );
  });

  it("carries no secret anywhere in the rendered output once a replace succeeds", async () => {
    vi.spyOn(client, "replaceProvider").mockResolvedValue(REPLACED);
    const { user } = mountReplace();

    await reachConfirmStep(user);
    // Not on screen even mid-flow, on the step that states the consequence.
    expect(document.body.textContent).not.toContain(SECRET);

    await user.click(screen.getByRole("button", { name: "Replace provider" }));
    await waitFor(() =>
      expect(client.replaceProvider).toHaveBeenCalled(),
    );

    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(document.body.textContent).not.toContain(SECRET.slice(-4));
  });

  it("does not advance to the confirmation step, or call the PUT, when a required field is missing", async () => {
    const spy = vi.spyOn(client, "replaceProvider");
    const { user } = mountReplace();

    await chooseKind(user, /Custom SMTP/);
    await user.type(screen.getByLabelText("SMTP username"), "billing@vaayu.example.com");
    await user.type(screen.getByLabelText("SMTP password"), SECRET);
    // No submission host typed.
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(
      await screen.findByText("An SMTP connection needs the host its provider submits on."),
    ).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Confirm the replacement" })).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it("closes without replacing anything when the customer cancels", async () => {
    const { user, onCancel } = mountReplace();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders nothing when closed", () => {
    render(<ConnectionWizard mode="replace" open={false} onConnected={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
