/**
 * The field's half of a reveal: what it shows masked, revealed and hidden again, which link a
 * revealed value becomes, what its one live region says, and where focus goes when the reveal
 * settles. The hook's half (state, timer, step-up) is tested in `@opsgrids/client`.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { REVEAL_VISIBLE_SECONDS, RevealableField } from "./RevealableField";
import type { RevealedFieldView } from "./RevealableField";

function field(overrides: Partial<RevealedFieldView> = {}): RevealedFieldView {
  return { value: null, notice: null, pending: false, justHidden: false, reveal: vi.fn(), hide: vi.fn(), ...overrides };
}

describe("RevealableField", () => {
  it("shows the masked value and no link until revealed, and asks the hook to reveal", async () => {
    const user = userEvent.setup();
    const state = field();
    render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={state} stepUpIdle />);
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reveal phone number" }));
    expect(state.reveal).toHaveBeenCalledTimes(1);
  });

  it("makes a revealed phone a tel: link of its digits, says it will hide, and hides on Hide", async () => {
    const user = userEvent.setup();
    const state = field({ value: "+91 98765 04821" });
    render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={state} stepUpIdle />);
    expect(screen.getByRole("link", { name: "+91 98765 04821" }).getAttribute("href")).toBe("tel:+919876504821");
    expect(screen.getByText(`Hides again in ${REVEAL_VISIBLE_SECONDS} seconds.`)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Hide phone number" }));
    expect(state.hide).toHaveBeenCalledTimes(1);
  });

  it("makes a revealed email a mailto: link", () => {
    render(
      <RevealableField kind="email" masked="p•••@example.com" canReveal field={field({ value: "priya@example.com" })} stepUpIdle />,
    );
    expect(screen.getByRole("link", { name: "priya@example.com" }).getAttribute("href")).toBe("mailto:priya@example.com");
  });

  it("shows a revealed text value as text, named by its own label and noun", () => {
    render(
      <RevealableField kind="text" label="PAN" noun="PAN" masked="••••••234Q" canReveal field={field({ value: "AAPPK1234Q" })} stepUpIdle />,
    );
    expect(screen.getByText("AAPPK1234Q")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("button", { name: "Hide PAN" })).toBeTruthy();
  });

  it("offers nothing without the permission, and says when nothing is on file", () => {
    const { rerender } = render(
      <RevealableField kind="phone" masked="•••••• 4821" canReveal={false} field={field()} stepUpIdle />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<RevealableField kind="phone" masked={null} canReveal field={field()} stepUpIdle />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("None on file")).toBeTruthy();
  });

  it("will not start a second reveal while a code is out", () => {
    render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field()} stepUpIdle={false} />);
    expect((screen.getByRole("button", { name: "Reveal phone number" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says a refusal and a hide in the live region under the row", () => {
    const { rerender, container } = render(
      <RevealableField
        kind="phone"
        masked="•••••• 4821"
        canReveal
        field={field({ notice: { tone: "error", text: "This lead is not assigned to you." } })}
        stepUpIdle
      />,
    );
    const region = container.querySelector("[aria-live='polite']");
    expect(region?.textContent).toBe("This lead is not assigned to you.");
    rerender(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ justHidden: true })} stepUpIdle />);
    expect(region?.textContent).toBe("Phone number hidden.");
  });

  it("puts focus back on its button when the reveal settles and focus had fallen to the page", () => {
    const { rerender } = render(
      <RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ pending: true })} stepUpIdle />,
    );
    (document.activeElement as HTMLElement | null)?.blur();
    rerender(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ value: "+91 98765 04821" })} stepUpIdle />);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Hide phone number" }));
  });
});
