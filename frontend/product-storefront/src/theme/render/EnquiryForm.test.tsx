/**
 * The one form on the buyer's surface: inline errors tied to their inputs, no double submit, the
 * entered data preserved on failure, and the outcome announced.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as client from "@/api/publicClient";
import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { defaultSettings } from "../sections";
import { EnquiryForm } from "./EnquiryForm";

function renderForm() {
  return render(
    <EnquiryForm
      settings={{ ...defaultSettings("enquiry-form"), storeSlug: "sunrise" }}
      products={PRODUCTS}
      categories={CATEGORY_LIST}
      stacked={false}
      width={1200}
      mode="trade"
    />,
  );
}

async function fillValidly(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Your name"), "Rajeev Bhatia");
  await user.type(screen.getByLabelText("Company"), "Sunrise Engineering");
  await user.type(screen.getByLabelText("Email"), "rajeev@sunrise.example.in");
  await user.type(screen.getByLabelText("What do you need?"), "200 coils of 2.5 sq mm FRLS");
}

describe("validation", () => {
  it("reports each missing field on the field itself, and does not call the API", async () => {
    const submit = vi.spyOn(client, "submitEnquiry");
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    const name = screen.getByLabelText("Your name");
    expect(name.getAttribute("aria-invalid")).toBe("true");
    const describedBy = name.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe("Enter your name.");

    expect(screen.getByText("Enter your company name.")).not.toBeNull();
    expect(screen.getByText("Enter an email address we can reply to.")).not.toBeNull();
    expect(screen.getByText("Tell us which line you need.")).not.toBeNull();
    expect(submit).not.toHaveBeenCalled();
  });

  it("rejects a malformed GSTIN with the format spelled out", async () => {
    const user = userEvent.setup();
    renderForm();

    await fillValidly(user);
    await user.type(screen.getByLabelText("GSTIN (optional)"), "NOT-A-GSTIN");
    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    expect(screen.getByText("A GSTIN is 15 characters, like 27AABCU9603R1ZX.")).not.toBeNull();
  });
});

describe("submitting", () => {
  it("disables the control while in flight and sends exactly once for two clicks", async () => {
    const submit = vi
      .spyOn(client, "submitEnquiry")
      .mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve({ id: "enq-1", received_at: "2026-09-18T00:00:00Z" }), 40),
          ),
      );
    const user = userEvent.setup();
    renderForm();

    await fillValidly(user);
    const button = screen.getByRole("button", { name: "Send enquiry" });
    await user.click(button);

    expect(screen.getByRole("button", { name: "Sending…" })).toHaveProperty("disabled", true);
    await user.click(screen.getByRole("button", { name: "Sending…" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).not.toBe(""));
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it("announces success in a live region and clears the form", async () => {
    vi.spyOn(client, "submitEnquiry").mockResolvedValue({
      id: "enq-2",
      received_at: "2026-09-18T00:00:00Z",
    });
    const user = userEvent.setup();
    renderForm();

    await fillValidly(user);
    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(
        "Enquiry received. We will reply to the email you gave us.",
      ),
    );
    expect(screen.getByLabelText("Your name")).toHaveProperty("value", "");
  });

  it("keeps everything the buyer typed when the send fails", async () => {
    vi.spyOn(client, "submitEnquiry").mockRejectedValue(
      new Error("We couldn't reach the server. Try again."),
    );
    const user = userEvent.setup();
    renderForm();

    await fillValidly(user);
    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "We couldn't reach the server. Try again.",
      ),
    );
    expect(screen.getByLabelText("What do you need?")).toHaveProperty(
      "value",
      "200 coils of 2.5 sq mm FRLS",
    );
    expect(screen.getByRole("button", { name: "Send enquiry" })).toHaveProperty("disabled", false);
  });
});

describe("WhatsApp marketing opt-in", () => {
  const LABEL = /Send me offers and updates on WhatsApp/;

  it("is unticked by default and sends false when left alone", async () => {
    const submit = vi
      .spyOn(client, "submitEnquiry")
      .mockResolvedValue({ id: "enq-3", received_at: "2026-09-18T00:00:00Z" });
    const user = userEvent.setup();
    renderForm();

    expect(screen.getByRole("checkbox", { name: LABEL })).toHaveProperty("checked", false);
    await fillValidly(user);
    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({ whatsappOptIn: false });
  });

  it("needs a number with its country code once ticked, said on the phone field", async () => {
    const submit = vi.spyOn(client, "submitEnquiry");
    const user = userEvent.setup();
    renderForm();

    await fillValidly(user);
    await user.click(screen.getByRole("checkbox", { name: LABEL }));
    await user.type(screen.getByLabelText("Phone (optional)"), "98765 43210");
    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    expect(screen.getByLabelText("Phone (optional)").getAttribute("aria-invalid")).toBe("true");
    expect(
      screen.getByText("For WhatsApp, add your number with its country code, like +91 98765 43210."),
    ).not.toBeNull();
    expect(submit).not.toHaveBeenCalled();
  });

  it("sends the tick with the number, then clears it", async () => {
    const submit = vi
      .spyOn(client, "submitEnquiry")
      .mockResolvedValue({ id: "enq-4", received_at: "2026-09-18T00:00:00Z" });
    const user = userEvent.setup();
    renderForm();

    await fillValidly(user);
    await user.type(screen.getByLabelText("Phone (optional)"), "+91 98765 43210");
    await user.click(screen.getByRole("checkbox", { name: LABEL }));
    await user.click(screen.getByRole("button", { name: "Send enquiry" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]?.[0]).toMatchObject({
      phone: "+91 98765 43210",
      whatsappOptIn: true,
    });
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: LABEL })).toHaveProperty("checked", false),
    );
  });
});
