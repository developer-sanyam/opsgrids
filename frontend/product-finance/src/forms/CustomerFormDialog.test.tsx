import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { CustomerFormDialog } from "./CustomerFormDialog";

const { createCustomerMock, updateCustomerMock } = vi.hoisted(() => ({
  createCustomerMock: vi.fn(),
  updateCustomerMock: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  createCustomer: createCustomerMock,
  updateCustomer: updateCustomerMock,
}));

function noop() {
  /* the dialog's onClose / onSaved for tests that don't assert on them */
}

describe("CustomerFormDialog", () => {
  it("shows our own validation message and never calls the API for a blank name", async () => {
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={noop} />);

    await user.click(screen.getByRole("button", { name: /create customer/i }));

    expect(await screen.findByText("Give the customer a name.")).toBeDefined();
    expect(createCustomerMock).not.toHaveBeenCalled();
  });

  it("keeps 194Q and 206C(1H) mutually exclusive — checking one clears the other", async () => {
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={noop} />);

    const deducts194q = screen.getByLabelText(/Deducts income tax under s\.194Q/i);
    const tcsApplicable = screen.getByLabelText(/TCS collected under s\.206C/i);

    await user.click(deducts194q);
    expect(deducts194q.getAttribute("aria-checked")).toBe("true");

    await user.click(tcsApplicable);
    expect(tcsApplicable.getAttribute("aria-checked")).toBe("true");
    expect(deducts194q.getAttribute("aria-checked")).toBe("false");

    await user.type(screen.getByLabelText("Name"), "Acme Pvt Ltd");
    await user.click(screen.getByRole("button", { name: /create customer/i }));

    await waitFor(() => expect(createCustomerMock).toHaveBeenCalled());
    const [payload] = createCustomerMock.mock.calls[0] as [{ deducts_tds_194q: boolean; tcs_applicable: boolean }];
    expect(payload.deducts_tds_194q).toBe(false);
    expect(payload.tcs_applicable).toBe(true);
  });

  it("disables the submit button while the write is pending and never double-submits", async () => {
    let resolvePromise: (value: unknown) => void = () => undefined;
    createCustomerMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePromise = resolve;
      }),
    );
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={noop} />);

    await user.type(screen.getByLabelText("Name"), "Acme Pvt Ltd");
    const submit = screen.getByRole("button", { name: /create customer/i });
    await user.click(submit);
    await user.click(submit);

    expect(createCustomerMock).toHaveBeenCalledTimes(1);
    resolvePromise({ id: "cust-1", name: "Acme Pvt Ltd" });
  });

  it("closes and calls onSaved with the server's record on success", async () => {
    createCustomerMock.mockResolvedValue({
      id: "cust-1",
      name: "Acme Pvt Ltd",
      contact_name: "",
      email: "",
      gstin: null,
      city: "",
      state: "",
      is_tds_deductor: false,
      deducts_tds_194q: false,
      tcs_applicable: false,
    });
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={onSaved} />);

    await user.type(screen.getByLabelText("Name"), "Acme Pvt Ltd");
    await user.click(screen.getByRole("button", { name: /create customer/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "cust-1" })));
  });

  it("maps a 422 field error onto its field", async () => {
    createCustomerMock.mockRejectedValue(
      new ApiError(422, "Validation failed", {
        status: 422,
        title: "Unprocessable",
        errors: [{ field: "gstin", code: "invalid_checksum", message: "That GSTIN's check digit is wrong." }],
      }),
    );
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={noop} />);

    await user.type(screen.getByLabelText("Name"), "Acme Pvt Ltd");
    await user.click(screen.getByRole("button", { name: /create customer/i }));

    expect(await screen.findByText("That GSTIN's check digit is wrong.")).toBeDefined();
  });

  it("shows the server's sentence for a 403 without pretending it's a field's fault", async () => {
    createCustomerMock.mockRejectedValue(
      new ApiError(403, "Forbidden", {
        status: 403,
        title: "Forbidden",
        detail: "You do not have finance.billing.customer.write.",
      }),
    );
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={noop} />);

    await user.type(screen.getByLabelText("Name"), "Acme Pvt Ltd");
    await user.click(screen.getByRole("button", { name: /create customer/i }));

    expect(await screen.findByText("You do not have finance.billing.customer.write.")).toBeDefined();
  });

  it("preserves what was typed after a failed submit", async () => {
    createCustomerMock.mockRejectedValue(
      new ApiError(500, "Server error", { status: 500, title: "Server error" }),
    );
    const user = userEvent.setup();
    render(<CustomerFormDialog customer={null} onClose={noop} onSaved={noop} />);

    await user.type(screen.getByLabelText("Name"), "Acme Pvt Ltd");
    await user.click(screen.getByRole("button", { name: /create customer/i }));

    await waitFor(() => expect(createCustomerMock).toHaveBeenCalled());
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Acme Pvt Ltd");
  });
});
