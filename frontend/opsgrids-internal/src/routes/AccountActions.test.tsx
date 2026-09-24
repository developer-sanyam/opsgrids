/**
 * The override dialogs behind an account's actions: suspend, reactivate, grant credit, set a
 * bespoke price, migrate a plan. Each writes to a customer's account, so what is pinned is the
 * part of a form that is easy to get wrong and expensive when it is:
 *
 * - **keyboard**: focus lands on the first field that wants an answer, Escape and Cancel close it,
 *   and focus returns to the button that opened it;
 * - **validation**: what is empty says so on that field and sends nothing, and money is parsed to
 *   integer minor units from the digits typed, never through a float;
 * - **failure**: the server's sentence, its reference and its field detail all reach the screen, the
 *   dialog stays open, and what was typed survives;
 * - **submission**: one request however many times the button is pressed, a visible working state,
 *   and on success the dialog closes, a toast says what was recorded, and the page reloads.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { renderConsole } from "@/testing-render";

const NORTHWIND = "org_a1f09c";
const VERTEX = "org_4b0aa6";
const DEMO = "org_905ffb";

type User = ReturnType<typeof userEvent.setup>;

async function openAccount(id = NORTHWIND) {
  const user = userEvent.setup();
  const utils = renderConsole(`/accounts/${id}`);
  await screen.findByRole("heading", { level: 1 });
  // The pricing actions wait for the catalog; nothing below needs to race it.
  await waitFor(() => expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(false));
  return { user, ...utils };
}

async function openDialog(user: User, button: string, title: string | RegExp) {
  await user.click(screen.getByRole("button", { name: button }));
  return screen.findByRole("dialog", { name: title });
}

function conflict(detail: string, extra: { errors?: { field: string; code: string; message: string }[] } = {}) {
  return new ApiError(422, detail, { status: 422, detail, request_id: "ovr-77", ...extra });
}

describe("suspend", () => {
  it("opens on the reason, says what it will do, and closes on Escape with focus back where it came from", async () => {
    const { user } = await openAccount();
    const opener = screen.getByRole("button", { name: "Suspend" });

    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");
    expect(within(dialog).getByText("De-entitles the account on its next access-token refresh (≤ 15 min).")).toBeDefined();
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Reason (required)"));

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("closes on Cancel without sending anything", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("says a missing reason is missing, on the field, and sends nothing", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");

    await user.click(within(dialog).getByRole("button", { name: "Suspend account" }));

    const field = within(dialog).getByLabelText("Reason (required)");
    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(field);
    expect(post).not.toHaveBeenCalled();
  });

  it("wears the danger tone, because submitting is itself the destructive act", async () => {
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");
    expect(within(dialog).getByRole("button", { name: "Suspend account" }).className).toContain("bg-negative");
  });

  it("records the suspension, closes, says what was recorded, and reloads the account", async () => {
    const post = vi.spyOn(client, "postOverride");
    const read = vi.spyOn(client, "getOrganization");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");

    await user.type(within(dialog).getByLabelText("Reason (required)"), "  Non-payment after dunning  ");
    await user.click(within(dialog).getByRole("button", { name: "Suspend account" }));

    // The reason goes out trimmed; the request is exactly what the operator asked for.
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith({ kind: "suspend", organization_id: NORTHWIND, reason: "Non-payment after dunning" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Override accepted.")).toBeDefined();
    expect(screen.getAllByText(/Simulated suspend override/).length).toBeGreaterThan(0);
    // The page re-read the account, so what it shows is what the server now says.
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  });

  it("keeps the dialog open on a refusal, says the server's sentence and reference, and keeps what was typed", async () => {
    vi.spyOn(client, "postOverride").mockRejectedValueOnce(
      new ApiError(503, "subscription-service is unavailable", { status: 503, detail: "subscription-service is unavailable", request_id: "ovr-503" }),
    );
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");

    await user.type(within(dialog).getByLabelText("Reason (required)"), "Non-payment");
    await user.click(within(dialog).getByRole("button", { name: "Suspend account" }));

    expect(await within(dialog).findByText("subscription-service is unavailable")).toBeDefined();
    expect(within(dialog).getByText("Reference: ovr-503")).toBeDefined();
    expect((within(dialog).getByLabelText("Reason (required)") as HTMLTextAreaElement).value).toBe("Non-payment");
    // Enabled again, and it is the same dialog: a retry is one press away.
    expect(within(dialog).getByRole("button", { name: "Suspend account" }).hasAttribute("disabled")).toBe(false);
    expect(screen.queryByText("Override accepted.")).toBeNull();
  });

  it("lands the server's field detail on the field rather than in a banner", async () => {
    vi.spyOn(client, "postOverride").mockRejectedValueOnce(
      conflict("Validation failed", { errors: [{ field: "reason", code: "string_too_long", message: "String should have at most 500 characters" }] }),
    );
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");

    await user.type(within(dialog).getByLabelText("Reason (required)"), "x");
    await user.click(within(dialog).getByRole("button", { name: "Suspend account" }));

    expect(await within(dialog).findByText("String should have at most 500 characters")).toBeDefined();
    expect(within(dialog).queryByRole("alert")).toBeNull();
  });

  it("sends once however many times the button is pressed, and says it is working", async () => {
    let release: (value: Awaited<ReturnType<typeof client.postOverride>>) => void = () => undefined;
    const post = vi.spyOn(client, "postOverride").mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Suspend", "Suspend Northwind Metals");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Non-payment");

    await user.click(within(dialog).getByRole("button", { name: "Suspend account" }));
    const working = await within(dialog).findByRole("button", { name: "Suspending…" });
    expect(working.hasAttribute("disabled")).toBe(true);
    expect(working.getAttribute("aria-busy")).toBe("true");
    await user.click(working);
    await user.keyboard("{Enter}");
    // Nothing can dismiss it mid-flight: the write is already gone.
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeDefined();

    expect(post).toHaveBeenCalledTimes(1);
    release({ status: "accepted", summary: "Suspended Northwind", audit_id: "aud_1" });
    expect(await screen.findByText("Override accepted.")).toBeDefined();
    expect(screen.getByText("Suspended Northwind · audit aud_1")).toBeDefined();
  });
});

describe("reactivate", () => {
  it("is the same form for a suspended account, in the ordinary tone", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount(VERTEX);
    const dialog = await openDialog(user, "Reactivate", "Reactivate Vertex Mining");

    expect(within(dialog).getByText("Restores entitlement; the customer regains access on the next token refresh.")).toBeDefined();
    expect(within(dialog).getByRole("button", { name: "Reactivate" }).className).toContain("bg-accent");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Payment recovered");
    await user.click(within(dialog).getByRole("button", { name: "Reactivate" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith({ kind: "reactivate", organization_id: VERTEX, reason: "Payment recovered" }));
    expect(await screen.findByText("Override accepted.")).toBeDefined();
  });
});

describe("grant credit", () => {
  it("opens on the amount, with the customer fixed, and offers the two kinds of grant", async () => {
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");

    expect(within(dialog).queryByLabelText("Customer")).toBeNull();
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Amount (₹)"));
    const type = within(dialog).getByRole("group", { name: "Grant type" });
    expect(within(type).getByRole("radio", { name: /Comp access/ })).toHaveProperty("checked", true);
    expect(within(type).getByRole("radio", { name: /Monetary credit/ })).toHaveProperty("checked", false);
  });

  it("says each thing that is wrong on its own field, focuses the first, and sends nothing", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");

    await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));

    expect(within(dialog).getByText("Enter an amount in rupees, like 45000.")).toBeDefined();
    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Amount (₹)"));
    expect(post).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText("Amount (₹)"), "0");
    await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));
    expect(within(dialog).getByText("Enter an amount greater than zero.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("sends the amount as integer paise, exactly, from the digits typed — and the expiry as the end of that day", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");

    await user.type(within(dialog).getByLabelText("Amount (₹)"), "450.5");
    await user.type(within(dialog).getByLabelText(/Expires/), "2026-12-31");
    await user.click(within(dialog).getByRole("radio", { name: /Monetary credit/ }));
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Goodwill");
    // The operator is told what the grant will be, in words, before it is sent.
    expect(within(dialog).getByText(/of credit, lapsing 2026-12-31\./)).toBeDefined();
    await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith({
      kind: "comp_grant",
      organization_id: NORTHWIND,
      amount_minor: 45_050,
      currency: "INR",
      is_comp: false,
      reason: "Goodwill",
      valid_until: "2026-12-31T23:59:59Z",
    });
    expect(await screen.findByText("Override accepted.")).toBeDefined();
  });

  it("sends no expiry when there is none, and says so", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");

    await user.type(within(dialog).getByLabelText("Amount (₹)"), "45000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Comp");
    expect(within(dialog).getByText(/of comp access, with no expiry\./)).toBeDefined();
    await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[0]).toMatchObject({ amount_minor: 4_500_000, is_comp: true, valid_until: null });
  });

  it("refuses an amount that is not one — a sign, letters, more decimals than the currency has", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");
    const amount = within(dialog).getByLabelText("Amount (₹)");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Comp");

    for (const bad of ["1.005", "-5", "abc"]) {
      await user.clear(amount);
      await user.type(amount, bad);
      await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));
      expect(within(dialog).getByText("Enter an amount in rupees, like 45000."), bad).toBeDefined();
    }
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps the dialog and what was typed on a refusal, and shows the server's sentence", async () => {
    vi.spyOn(client, "postOverride").mockRejectedValueOnce(conflict("billing-service is unavailable"));
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");

    await user.type(within(dialog).getByLabelText("Amount (₹)"), "45000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Comp");
    await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));

    expect(await within(dialog).findByText("billing-service is unavailable")).toBeDefined();
    expect((within(dialog).getByLabelText("Amount (₹)") as HTMLInputElement).value).toBe("45000");
    expect((within(dialog).getByLabelText("Reason (required)") as HTMLTextAreaElement).value).toBe("Comp");
  });

  it("lands a server-named amount error on the amount field", async () => {
    vi.spyOn(client, "postOverride").mockRejectedValueOnce(
      conflict("Validation failed", { errors: [{ field: "amount_minor", code: "greater_than_equal", message: "Input should be greater than or equal to 0" }] }),
    );
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Grant credit", "Grant credit — Northwind Metals");

    await user.type(within(dialog).getByLabelText("Amount (₹)"), "5");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Comp");
    await user.click(within(dialog).getByRole("button", { name: "Grant credit" }));

    expect(await within(dialog).findByText("Input should be greater than or equal to 0")).toBeDefined();
  });
});

describe("bespoke price", () => {
  it("offers the plans of the product chosen, and only those", async () => {
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Bespoke price", "Bespoke price — Northwind Metals");

    const product = within(dialog).getByLabelText("Product") as HTMLSelectElement;
    const plan = within(dialog).getByLabelText("Base plan to clone") as HTMLSelectElement;
    const before = [...plan.options].map((o) => o.textContent);
    expect(before.length).toBeGreaterThan(0);

    const other = [...product.options].find((o) => o.value !== product.value);
    if (other === undefined) throw new Error("no second product");
    await user.selectOptions(product, other.value);

    const after = [...(within(dialog).getByLabelText("Base plan to clone") as HTMLSelectElement).options].map((o) => o.textContent);
    expect(after).not.toEqual(before);
  });

  it("says a missing price and reason are missing, focusing the price first", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Bespoke price", "Bespoke price — Northwind Metals");

    await user.click(within(dialog).getByRole("button", { name: "Create & pin price" }));

    expect(within(dialog).getByText("Enter a price in rupees, like 420000.")).toBeDefined();
    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Bespoke monthly price (₹)"));
    expect(post).not.toHaveBeenCalled();
  });

  it("creates the version, in integer paise, restricted to this customer", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount();
    const dialog = await openDialog(user, "Bespoke price", "Bespoke price — Northwind Metals");

    await user.type(within(dialog).getByLabelText("Bespoke monthly price (₹)"), "420000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Negotiated enterprise rate");
    expect(within(dialog).getByText(/Creates a version restricted to Northwind Metals at/)).toBeDefined();
    await user.click(within(dialog).getByRole("button", { name: "Create & pin price" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const request = post.mock.calls[0]?.[0];
    expect(request).toMatchObject({
      kind: "bespoke_price",
      organization_id: NORTHWIND,
      amount_minor: 42_000_000,
      currency: "INR",
      reason: "Negotiated enterprise rate",
    });
    expect(request && "base_plan_id" in request && request.base_plan_id).toBeTruthy();
    expect(await screen.findByText("Override accepted.")).toBeDefined();
  });
});

describe("migrate plan", () => {
  it("offers the account's live subscriptions, and only published versions it may move to", async () => {
    const { user } = await openAccount("org_d4c33f");
    const dialog = await openDialog(user, "Migrate plan", "Migrate plan — Kaveri Steel");

    const subscription = within(dialog).getByLabelText("Subscription") as HTMLSelectElement;
    expect(subscription.options.length).toBeGreaterThan(1);
    expect(within(dialog).getByLabelText("Target plan version")).toBeDefined();
  });

  it("says a missing reason is missing, and sends nothing", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount("org_d4c33f");
    const dialog = await openDialog(user, "Migrate plan", "Migrate plan — Kaveri Steel");

    await user.click(within(dialog).getByRole("button", { name: "Migrate plan" }));

    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("pins the subscription to the version chosen, and records why", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user } = await openAccount("org_d4c33f");
    const dialog = await openDialog(user, "Migrate plan", "Migrate plan — Kaveri Steel");

    await user.type(within(dialog).getByLabelText("Reason (required)"), "Upgrade at renewal");
    await user.click(within(dialog).getByRole("button", { name: "Migrate plan" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[0]).toMatchObject({
      kind: "plan_migration",
      organization_id: "org_d4c33f",
      reason: "Upgrade at renewal",
    });
    expect(await screen.findByText("Override accepted.")).toBeDefined();
  });

  it("has nothing to migrate for an account with no live subscription, and says so with a way out", async () => {
    const { user } = await openAccount(VERTEX);
    await user.click(screen.getByRole("button", { name: "Migrate plan" }));

    const dialog = await screen.findByRole("dialog", { name: "Migrate plan — Vertex Mining" });
    expect(within(dialog).getByText("This account has no active subscription to migrate. Reactivate it first.")).toBeDefined();
    expect(within(dialog).queryByLabelText("Subscription")).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when the product has no other published version, on submit", async () => {
    const post = vi.spyOn(client, "postOverride");
    const catalog = await client.getCatalog();
    vi.spyOn(client, "getCatalog").mockResolvedValue({ ...catalog, versions: [] });
    const { user } = await openAccount(DEMO);
    const dialog = await openDialog(user, "Migrate plan", /Migrate plan — /);

    expect(within(dialog).getByText("No other published version is available for this product.")).toBeDefined();
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Anything");
    await user.click(within(dialog).getByRole("button", { name: "Migrate plan" }));

    expect(await within(dialog).findByRole("alert")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });
});
