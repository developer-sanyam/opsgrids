/**
 * The step-up dialog is a code field with a very short fuse and a very expensive mistake: a code
 * lasts thirty seconds, and every wrong one counts toward locking the account. So what is worth
 * pinning is the keyboard and paste behaviour that spends none of it, the field that empties when
 * a code is refused, the buttons that go inert while a code is out, and the two states in which no
 * code can work.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";
import { StepUpDialog } from "./StepUpDialog";
import type { StepUpDialogError, StepUpDialogProps, StepUpMfaState } from "./StepUpDialog";

const PROMPT =
  "Enter the 6-digit code from your authenticator app to reveal this. Codes can be used once: if you just signed in, wait for the next one.";

function props(overrides: Partial<StepUpDialogProps> = {}): StepUpDialogProps {
  return {
    open: true,
    busy: false,
    mfaState: "ready",
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    securityHref: "/account/security",
    ...overrides,
  };
}

const codeField = () => screen.getByRole("textbox", { name: /6-digit code/ });

/** A host the way `useStepUp` drives it: a trigger, and answers that arrive after a request. */
function Host({
  answer,
  initialState = "ready",
  onSubmit,
}: {
  answer?: { error?: StepUpDialogError; mfaState?: StepUpMfaState } | undefined;
  initialState?: StepUpMfaState;
  onSubmit?: (code: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<StepUpDialogError | undefined>(undefined);
  const [mfaState, setMfaState] = useState<StepUpMfaState>(initialState);

  return (
    <>
      <Button onClick={() => setOpen(true)}>Reveal phone</Button>
      <StepUpDialog
        open={open}
        busy={busy}
        error={error}
        mfaState={mfaState}
        securityHref="/account/security"
        onCancel={() => setOpen(false)}
        onSubmit={(code) => {
          onSubmit?.(code);
          setError(undefined);
          setBusy(true);
          window.setTimeout(() => {
            setBusy(false);
            if (answer === undefined) {
              setOpen(false);
              return;
            }
            setError(answer.error);
            if (answer.mfaState !== undefined) setMfaState(answer.mfaState);
          }, 0);
        }}
      />
    </>
  );
}

describe("StepUpDialog: the code prompt", () => {
  it("says what to do in the approved words, and names the field for what goes in it", () => {
    render(<StepUpDialog {...props()} />);

    const dialog = screen.getByRole("dialog", { name: "Confirm it's you" });
    expect(dialog.getAttribute("aria-describedby")).not.toBeNull();
    expect(screen.getByText(PROMPT)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verify" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("asks for a one-time code in a field the phone and the keychain know how to fill", () => {
    render(<StepUpDialog {...props()} />);

    const field = codeField();
    expect(field.getAttribute("inputmode")).toBe("numeric");
    expect(field.getAttribute("autocomplete")).toBe("one-time-code");
    expect(field.hasAttribute("required")).toBe(true);
  });

  it("puts focus in the code field on open", () => {
    render(<StepUpDialog {...props()} />);
    expect(document.activeElement).toBe(codeField());
  });

  it("renders nothing while closed", () => {
    render(<StepUpDialog {...props({ open: false })} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("StepUpDialog: entering the code", () => {
  it("submits on the sixth digit, without a button press", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<StepUpDialog {...props({ onSubmit })} />);

    await user.type(codeField(), "48291");
    expect(onSubmit).not.toHaveBeenCalled();
    await user.type(codeField(), "3");

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("482913");
  });

  it("takes a pasted code however it was copied, and submits it once", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<StepUpDialog {...props({ onSubmit })} />);

    await user.click(codeField());
    await user.paste("482 913");

    expect(codeField()).toHaveProperty("value", "482913");
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("482913");
  });

  it("drops anything that is not a digit, and anything past the sixth", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<StepUpDialog {...props({ onSubmit })} />);

    await user.type(codeField(), "48a-29");
    expect(codeField()).toHaveProperty("value", "4829");
    await user.type(codeField(), "1399");

    expect(codeField()).toHaveProperty("value", "482913");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("does not also submit a form the screen opened it from", async () => {
    const user = userEvent.setup();
    const outer = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    render(
      <form onSubmit={outer}>
        <StepUpDialog {...props({ onSubmit: vi.fn() })} />
      </form>,
    );

    await user.type(codeField(), "4829{Enter}");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(outer).not.toHaveBeenCalled();
  });

  it("answers Enter or Verify on an unfinished code with the field's own message, and sends nothing", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<StepUpDialog {...props({ onSubmit })} />);

    await user.type(codeField(), "4829{Enter}");

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("Enter all 6 digits.")).toBeTruthy();
    expect(codeField().getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(codeField());

    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(onSubmit).not.toHaveBeenCalled();

    // Typing on clears the message.
    await user.type(codeField(), "1");
    expect(screen.queryByText("Enter all 6 digits.")).toBeNull();
  });

  it("submits a full code that is still in the field when Enter or Verify is used", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<StepUpDialog {...props({ onSubmit })} />);
    await user.type(codeField(), "482913");

    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(onSubmit.mock.calls).toEqual([["482913"], ["482913"], ["482913"]]);
  });
});

describe("StepUpDialog: while the code is with the server", () => {
  it("shows the field read-only, Verify as verifying, and refuses every way of closing", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const onSubmit = vi.fn();
    render(<StepUpDialog {...props({ busy: true, onCancel, onSubmit })} />);

    expect(codeField().hasAttribute("readonly")).toBe(true);
    const verify = screen.getByRole("button", { name: "Verifying…" });
    expect(verify.hasAttribute("disabled")).toBe(true);
    expect(verify.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(true);

    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Close dialog" }));
    await user.type(codeField(), "123456");

    expect(onCancel).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("says it is verifying to a screen reader, in the same live region that will say the answer", () => {
    render(<StepUpDialog {...props({ busy: true })} />);

    const status = screen.getByText("Verifying…", { selector: ".sr-only" });
    expect(status.closest("[aria-live]")).not.toBeNull();
  });
});

describe("StepUpDialog: a code that was refused", () => {
  it("says so under the field, in a live region, and stays open", () => {
    render(<StepUpDialog {...props({ error: "wrong-code" })} />);

    const message = screen.getByText(
      "That code didn't work. Wait for the next code in your authenticator app, then try again.",
    );
    expect(message.closest("[aria-live]")).not.toBeNull();
    expect(codeField().getAttribute("aria-invalid")).toBe("true");
    expect(codeField().getAttribute("aria-describedby")).toContain(message.id);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("says the lockout in the approved words", () => {
    render(<StepUpDialog {...props({ error: "locked" })} />);
    expect(screen.getByText("Too many attempts. Try again in a few minutes.")).toBeTruthy();
  });

  it("says a dropped connection is not the code's fault", () => {
    render(<StepUpDialog {...props({ error: "unavailable" })} />);
    expect(screen.getByText("We couldn't check that code. Check your connection and try again.")).toBeTruthy();
  });

  it("empties the field and puts focus back in it after a wrong code, so the refused digits cannot be sent again", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Host answer={{ error: "wrong-code" }} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    await user.type(codeField(), "000000");

    await screen.findByText(/That code didn't work/);
    expect(codeField()).toHaveProperty("value", "");
    expect(document.activeElement).toBe(codeField());

    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("empties the field after a lockout as well", async () => {
    const user = userEvent.setup();
    render(<Host answer={{ error: "locked" }} />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    await user.type(codeField(), "000000");

    await screen.findByText("Too many attempts. Try again in a few minutes.");
    expect(codeField()).toHaveProperty("value", "");
  });

  it("empties the field after a timeout or a dropped connection too, since the code may already be spent", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Host answer={{ error: "unavailable" }} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    await user.type(codeField(), "482913");

    await screen.findByText(/We couldn't check that code/);
    expect(codeField()).toHaveProperty("value", "");
    expect(document.activeElement).toBe(codeField());

    // Enter on the emptied field sends nothing: the same digits cannot go out a second time.
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Enter all 6 digits.")).toBeTruthy();
  });

  it("returns focus to the field when the button that held it was disabled by the request", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = render(<StepUpDialog {...props({ onSubmit })} />);
    await user.type(codeField(), "482913");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(onSubmit).toHaveBeenCalledTimes(2);

    rerender(<StepUpDialog {...props({ onSubmit, busy: true })} />);
    rerender(<StepUpDialog {...props({ onSubmit, busy: false, error: "unavailable" })} />);

    expect(document.activeElement).toBe(codeField());
  });
});

describe("StepUpDialog: when no code can work", () => {
  it("explains a missing second factor and links to the Security page, with no field to type in", () => {
    render(<StepUpDialog {...props({ mfaState: "not-enrolled", securityHref: "https://app.opsgrids.com/account/security" })} />);

    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    expect(
      screen.getByText(/Two-factor authentication is required to reveal this, and it isn't set up on your account yet\./),
    ).toBeTruthy();
    const link = screen.getByRole("link", { name: "Security page (opens in a new tab)" });
    expect(link.getAttribute("href")).toBe("https://app.opsgrids.com/account/security");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
  });

  it("explains that single sign-on accounts cannot reveal this, with nowhere to go", () => {
    render(<StepUpDialog {...props({ mfaState: "sso" })} />);

    expect(screen.getByText("Revealing this is unavailable for single sign-on accounts.")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
  });

  it("closes from Close in either state", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const { rerender } = render(<StepUpDialog {...props({ mfaState: "sso", onCancel })} />);
    await user.click(screen.getByRole("button", { name: "Close" }));
    rerender(<StepUpDialog {...props({ mfaState: "not-enrolled", onCancel })} />);
    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("lands focus on the heading when it opens in one of these states, to be read from the top", () => {
    render(<StepUpDialog {...props({ mfaState: "not-enrolled" })} />);
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Confirm it's you" }));
  });

  it("moves focus to the Security link when the answer arrives with the dialog already open", async () => {
    const user = userEvent.setup();
    render(<Host answer={{ mfaState: "not-enrolled" }} />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    await user.type(codeField(), "482913");

    const link = await screen.findByRole("link", { name: /Security page/ });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.activeElement).toBe(link);
    // And it is said, not just shown.
    expect(link.closest("[aria-live]")).not.toBeNull();
  });

  it("explains that the authenticator was set up after this session began, with nowhere to type", () => {
    render(<StepUpDialog {...props({ mfaState: "reauthenticate" })} />);

    expect(
      screen.getByText("Your authenticator was set up after you signed in. Sign out and sign in again, then try once more."),
    ).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
  });

  it("moves focus to Close when the answer is to sign in again", async () => {
    const user = userEvent.setup();
    render(<Host answer={{ mfaState: "reauthenticate" }} />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    await user.type(codeField(), "482913");

    const close = await screen.findByRole("button", { name: "Close" });
    expect(document.activeElement).toBe(close);
  });

  it("moves focus to Close when the answer is that this is a single sign-on account", async () => {
    const user = userEvent.setup();
    render(<Host answer={{ mfaState: "sso" }} />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    await user.type(codeField(), "482913");

    const close = await screen.findByRole("button", { name: "Close" });
    expect(document.activeElement).toBe(close);
  });
});

describe("StepUpDialog: leaving", () => {
  it("cancels from Cancel, from Escape and from the close control", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<StepUpDialog {...props({ onCancel })} />);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Close dialog" }));

    expect(onCancel).toHaveBeenCalledTimes(3);
  });

  it("does not close on a click outside, so a code in progress is not lost to a stray click", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const { baseElement } = render(<StepUpDialog {...props({ onCancel })} />);

    const backdrop = baseElement.querySelector('[aria-hidden="true"]');
    expect(backdrop).not.toBeNull();
    if (backdrop !== null) await user.click(backdrop);

    expect(onCancel).not.toHaveBeenCalled();
  });

  it("returns focus to the control that opened it", async () => {
    const user = userEvent.setup();
    render(<Host />);
    const trigger = screen.getByRole("button", { name: "Reveal phone" });

    await user.click(trigger);
    expect(document.activeElement).toBe(codeField());
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("returns focus to the trigger after a successful code too", async () => {
    const user = userEvent.setup();
    render(<Host />);
    const trigger = screen.getByRole("button", { name: "Reveal phone" });

    await user.click(trigger);
    await user.type(codeField(), "482913");

    // Focus goes back when the dialog's effect is cleaned up, which can land a tick after the
    // dialog has left the page.
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("starts with an empty field every time it opens", async () => {
    const user = userEvent.setup();
    render(<Host answer={{ error: "unavailable" }} />);
    const trigger = screen.getByRole("button", { name: "Reveal phone" });

    await user.click(trigger);
    await user.type(codeField(), "482913");
    await screen.findByText(/We couldn't check that code/);
    await user.keyboard("{Escape}");
    await user.click(trigger);

    expect(codeField()).toHaveProperty("value", "");
  });

  it("keeps the keyboard inside while it is open: Tab wraps from Verify back to the close control", async () => {
    const user = userEvent.setup();
    render(<StepUpDialog {...props()} />);
    const dialog = screen.getByRole("dialog");

    for (let index = 0; index < 8; index += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
  });
});
