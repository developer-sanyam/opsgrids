/**
 * Fixture mode (`VITE_USE_FIXTURES=true`, dev builds) has no auth service, and `useStepUp` always
 * calls the real `/auth/mfa/step-up`. So the field must not go through the step-up there: it calls
 * the fixture reveal directly, and no dialog is ever asked for. The flag is a constant of the api
 * client, so this file makes it true for the whole file; the live path is every other test.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UseStepUpResult } from "@opsgrids/client";

import { RevealField } from "./RevealField";

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  USE_FIXTURES: true,
}));

afterEach(() => {
  vi.restoreAllMocks();
});

function refusingStepUp(): UseStepUpResult {
  return {
    run: vi.fn(() => Promise.reject(new Error("Fixture mode must not ask for a code."))),
    state: "idle",
    clear: vi.fn(),
    dialogProps: {
      open: false,
      busy: false,
      error: undefined,
      mfaState: "ready",
      onSubmit: () => undefined,
      onCancel: () => undefined,
      securityHref: "/account/security",
    },
  };
}

describe("RevealField in fixture mode", () => {
  it("reveals through the fixture call directly, never asking the step-up for a code", async () => {
    const user = userEvent.setup();
    const stepUp = refusingStepUp();
    const load = vi.fn().mockResolvedValue("+91 98765 04821");
    render(
      <RevealField
        kind="phone"
        masked="•••••• 4821"
        subject="lead"
        canReveal
        stepUp={stepUp}
        resetKey="lead-1"
        load={load}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Reveal phone number" }));

    expect(await screen.findByRole("link", { name: "+91 98765 04821" })).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(1);
    expect(stepUp.run).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Hide phone number" })).toBeTruthy();
  });
});
