/**
 * The field's half of a reveal: what it shows masked and revealed, which link a revealed value
 * becomes (and that the target is tappable), what its one live region says, and where focus goes
 * when the reveal settles. The hook's half (state, call, session cache) is tested in
 * `@opsgrids/client`. There is no code prompt, no Hide and no timer (ADR-0023 addendum).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RevealableField } from "./RevealableField";
import type { RevealedFieldView } from "./RevealableField";

function field(overrides: Partial<RevealedFieldView> = {}): RevealedFieldView {
  return { value: null, notice: null, pending: false, reveal: vi.fn(), ...overrides };
}

describe("RevealableField", () => {
  it("shows the masked value and no link until revealed, and asks the hook to reveal on one tap", async () => {
    const user = userEvent.setup();
    const state = field();
    render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={state} />);
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reveal phone number" }));
    expect(state.reveal).toHaveBeenCalledTimes(1);
  });

  it("shows the value after the tap with no code dialog, and leaves no Hide and no timer wording", () => {
    render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ value: "+91 98765 04821" })} />);
    expect(screen.getByText("+91 98765 04821")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/Hides again/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/one-time code/i);
  });

  it("makes a revealed phone a tel: link of its digits with a 44px target", () => {
    render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ value: "+91 98765 04821" })} />);
    const link = screen.getByRole("link", { name: "+91 98765 04821" });
    expect(link.getAttribute("href")).toBe("tel:+919876504821");
    expect(link.className).toContain("min-h-11");
  });

  it("makes a revealed email a mailto: link with a 44px target", () => {
    render(<RevealableField kind="email" masked="p•••@example.com" canReveal field={field({ value: "priya@example.com" })} />);
    const link = screen.getByRole("link", { name: "priya@example.com" });
    expect(link.getAttribute("href")).toBe("mailto:priya@example.com");
    expect(link.className).toContain("min-h-11");
  });

  it("shows a revealed text value as text, named by its own label and noun", () => {
    const { rerender } = render(
      <RevealableField kind="text" label="PAN" noun="PAN" masked="••••••234Q" canReveal field={field()} />,
    );
    expect(screen.getByRole("button", { name: "Reveal PAN" })).toBeTruthy();
    rerender(
      <RevealableField kind="text" label="PAN" noun="PAN" masked="••••••234Q" canReveal field={field({ value: "AAPPK1234Q" })} />,
    );
    expect(screen.getByText("AAPPK1234Q")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("offers nothing without the permission, and says when nothing is on file", () => {
    const { rerender } = render(<RevealableField kind="phone" masked="•••••• 4821" canReveal={false} field={field()} />);
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<RevealableField kind="phone" masked={null} canReveal field={field()} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("None on file")).toBeTruthy();
  });

  it("says a refusal, and that a value is showing, in the live region under the row", () => {
    const { rerender, container } = render(
      <RevealableField
        kind="phone"
        masked="•••••• 4821"
        canReveal
        field={field({ notice: { tone: "error", text: "This lead is not assigned to you." } })}
      />,
    );
    const region = container.querySelector("[aria-live='polite']");
    expect(region?.textContent).toBe("This lead is not assigned to you.");
    rerender(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ value: "+91 98765 04821" })} />);
    expect(region?.textContent).toBe("Phone number revealed.");
  });

  it("puts focus on the value when the reveal settles and focus had fallen to the page", () => {
    const { rerender } = render(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ pending: true })} />);
    (document.activeElement as HTMLElement | null)?.blur();
    rerender(<RevealableField kind="phone" masked="•••••• 4821" canReveal field={field({ value: "+91 98765 04821" })} />);
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "+91 98765 04821" }));
  });
});
