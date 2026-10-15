import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { GstRegistration } from "@/api/types";

import { GstRegistrationFormDialog } from "./GstRegistrationFormDialog";

const { setMock } = vi.hoisted(() => ({ setMock: vi.fn() }));
vi.mock("@/api/client", () => ({ setGstRegistration: setMock }));

const REGISTRATION: GstRegistration = {
  id: "r-1",
  gstin: "27AABCA9012D1Z5",
  state_code: "27",
  state_name: "Maharashtra",
  legal_name: "Acme Pvt Ltd",
  trade_name: "",
  registration_type: "regular",
  is_primary: true,
  address_line1: "12 FC Road",
  address_line2: "",
  city: "Pune",
  pincode: "411004",
};

function noop() {
  /* callbacks the test does not assert on */
}

describe("GstRegistrationFormDialog confirmation", () => {
  it("saves an address-only edit without asking to confirm the registration", async () => {
    setMock.mockReset();
    setMock.mockResolvedValue(REGISTRATION);
    const user = userEvent.setup();
    render(<GstRegistrationFormDialog registration={REGISTRATION} onClose={noop} onSaved={noop} />);
    const city = screen.getByLabelText(/^City/);
    await user.clear(city);
    await user.type(city, "Mumbai");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(setMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("Set this GST registration?")).toBeNull();
  });

  it("still confirms when the registration number changes", async () => {
    setMock.mockReset();
    const user = userEvent.setup();
    render(<GstRegistrationFormDialog registration={REGISTRATION} onClose={noop} onSaved={noop} />);
    const number = screen.getByLabelText(/^GSTIN/);
    await user.clear(number);
    await user.type(number, "29AABCA9012D1Z3");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Set this GST registration?")).toBeTruthy();
    expect(setMock).not.toHaveBeenCalled();
  });
});
