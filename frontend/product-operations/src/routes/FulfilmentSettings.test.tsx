/**
 * The fulfilment settings screen (INVENTORY-02): anyone who can read orders sees the policy, only a
 * holder of the write permission can change it, bad input stays on its field, and the server's
 * field errors land on the control they are about.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { renderScreen } from "@/test-utils/renderScreen";
import { FulfilmentSettingsPage } from "./FulfilmentSettings";

const getFulfilmentSettings = vi.fn();
const putFulfilmentSettings = vi.fn();

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getFulfilmentSettings: (...args: unknown[]) => getFulfilmentSettings(...args),
  putFulfilmentSettings: (...args: unknown[]) => putFulfilmentSettings(...args),
  listWarehouses: () => Promise.resolve({ items: [{ id: "wh-1", name: "Main store" }], total: 1, page: 1, size: 200 }),
}));

const WRITE = "operations.fulfilment.settings.write:ORG";

const DEFAULTS = {
  warehouse_id: null,
  source: "default",
  invoice_timing: "on_delivery",
  payment_defaults: {},
  offer_timeout_seconds: 300,
  offer_rounds: 3,
  require_pickup_code: false,
  default_tax_rate_pct: "0.00",
  auto_assignment_enabled: false,
  task_sla_minutes: 60,
  alert_email: null,
  alert_phone: null,
  updated_at: null,
  updated_by: null,
};

beforeEach(() => {
  getFulfilmentSettings.mockReset();
  putFulfilmentSettings.mockReset();
  getFulfilmentSettings.mockResolvedValue(DEFAULTS);
});

describe("FulfilmentSettingsPage", () => {
  it("is read-only without the write permission", async () => {
    renderScreen(<FulfilmentSettingsPage />, []);
    const field = (await screen.findByLabelText(/Default tax rate/)) as HTMLInputElement;
    expect(field.readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
    expect(screen.getByText(/You can see these settings but not change them/)).toBeDefined();
    expect(screen.getByText(/built-in defaults/)).toBeDefined();
  });

  it("saves the whole policy, invoicing on delivery", async () => {
    putFulfilmentSettings.mockResolvedValue({ ...DEFAULTS, source: "organization", updated_at: "2026-10-12T09:00:00Z" });
    const user = userEvent.setup();
    renderScreen(<FulfilmentSettingsPage />, [WRITE]);

    const tax = await screen.findByLabelText(/Default tax rate/);
    await user.clear(tax);
    await user.type(tax, "18");
    await user.selectOptions(screen.getByLabelText(/Manual orders: payment term/), "credit");
    await user.click(screen.getByRole("switch", { name: "Require a pickup code" }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() =>
      expect(putFulfilmentSettings).toHaveBeenCalledWith(
        {
          invoice_timing: "on_delivery",
          payment_defaults: { walk_in: "cash", phone: "cash", manual: "credit", sales: "cash" },
          offer_timeout_seconds: 300,
          offer_rounds: 3,
          require_pickup_code: true,
          default_tax_rate_pct: "18",
          auto_assignment_enabled: false,
          task_sla_minutes: 60,
          alert_email: null,
          alert_phone: null,
        },
        undefined,
      ),
    );
  });

  it("keeps a bad figure on its own field without calling the server", async () => {
    const user = userEvent.setup();
    renderScreen(<FulfilmentSettingsPage />, [WRITE]);

    const rounds = await screen.findByLabelText(/Offer rounds/);
    await user.clear(rounds);
    await user.type(rounds, "50");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText("Enter a whole number from 1 to 10.")).toBeDefined();
    expect(putFulfilmentSettings).not.toHaveBeenCalled();
  });

  it("shows the server's sentence on the field it is about", async () => {
    putFulfilmentSettings.mockRejectedValue(
      new ApiError(422, "x", {
        type: "https://errors.opsgrids.dev/validation",
        status: 422,
        detail: "Validation failed",
        errors: [{ field: "alert_email", code: "value_error", message: "value is not a valid email address" }],
      }),
    );
    const user = userEvent.setup();
    renderScreen(<FulfilmentSettingsPage />, [WRITE]);

    await user.type(await screen.findByLabelText(/Alert email/), "ops@example.com");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText("value is not a valid email address")).toBeDefined();
  });

  it("loads one warehouse's settings when it is chosen", async () => {
    const user = userEvent.setup();
    renderScreen(<FulfilmentSettingsPage />, [WRITE]);

    const scope = await screen.findByLabelText("Applies to");
    await screen.findByRole("option", { name: "Main store" });
    getFulfilmentSettings.mockResolvedValue({ ...DEFAULTS, source: "organization", warehouse_id: "wh-1" });
    await user.selectOptions(scope, "wh-1");

    await waitFor(() => expect(getFulfilmentSettings).toHaveBeenLastCalledWith("wh-1"));
    expect(await screen.findByText(/follows the organization's settings/)).toBeDefined();
  });

  it("has an error state with a retry", async () => {
    getFulfilmentSettings.mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    const user = userEvent.setup();
    renderScreen(<FulfilmentSettingsPage />, []);
    expect(await screen.findByText("Couldn't load the fulfilment settings")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText(/built-in defaults/)).toBeDefined();
  });
});
