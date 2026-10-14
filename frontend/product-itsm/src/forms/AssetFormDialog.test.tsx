/**
 * The asset form's cost field, in each currency's own decimals: what it shows for a stored cost,
 * what it sends for a typed one, and what it refuses. INR and USD behave exactly as they did when
 * every currency was taken to have two decimals; JPY and KWD are what that assumption got wrong.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { Asset } from "@/api/types";
import { AssetFormDialog } from "./AssetFormDialog";

function asset(over: Partial<Asset> = {}): Asset {
  return {
    tag: "LT-2041",
    name: "Dell Latitude 7440",
    type: "laptop",
    status: "in_use",
    owner: null,
    location: "",
    serial: "",
    purchased_at: null,
    warranty_expires_at: null,
    cost_minor: 8_450_000,
    cost_currency: "INR",
    open_incident_ids: [],
    ...over,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function openEdit(existing: Asset) {
  vi.spyOn(client, "updateAsset").mockResolvedValue(existing);
  const onSaved = vi.fn();
  render(<AssetFormDialog asset={existing} onClose={vi.fn()} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved };
}

function openCreate() {
  vi.spyOn(client, "createAsset").mockResolvedValue(asset());
  render(<AssetFormDialog asset={null} onClose={vi.fn()} onSaved={vi.fn()} />);
  return userEvent.setup();
}

async function fillNewAsset(user: ReturnType<typeof userEvent.setup>, cost: string, currency: string) {
  await user.type(screen.getByLabelText(/^Tag/), "SRV-0042");
  await user.type(screen.getByLabelText(/^Name/), "Rack server");
  await user.type(screen.getByLabelText(/^Cost/), cost);
  const code = screen.getByLabelText(/^Currency/);
  await user.clear(code);
  await user.type(code, currency);
}

describe("AssetFormDialog: showing a stored cost", () => {
  it.each([
    ["INR", 8_450_000, "84500"],
    ["INR", 2950, "29.50"],
    ["USD", 105, "1.05"],
    ["JPY", 150_000, "150000"],
    ["KWD", 1250, "1.250"],
    ["KWD", 2000, "2"],
  ])("shows %s %i minor units as %s", (currency, minor, shown) => {
    openEdit(asset({ cost_minor: minor, cost_currency: currency }));
    expect(screen.getByLabelText(/^Cost/)).toHaveProperty("value", shown);
  });
});

describe("AssetFormDialog: saving a cost", () => {
  it("leaves a stored yen cost exactly as it was when the form is saved untouched", async () => {
    const { user } = openEdit(asset({ cost_minor: 150_000, cost_currency: "JPY" }));
    await user.type(screen.getByLabelText(/^Name/), " (spare)");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(client.updateAsset).toHaveBeenCalledTimes(1));
    expect(client.updateAsset).toHaveBeenCalledWith(
      "LT-2041",
      expect.objectContaining({ cost_minor: 150_000, cost_currency: "JPY" }),
    );
  });

  it("leaves a stored dinar cost exactly as it was when the form is saved untouched", async () => {
    const { user } = openEdit(asset({ cost_minor: 1250, cost_currency: "KWD" }));
    await user.type(screen.getByLabelText(/^Name/), " (spare)");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(client.updateAsset).toHaveBeenCalledTimes(1));
    expect(client.updateAsset).toHaveBeenCalledWith(
      "LT-2041",
      expect.objectContaining({ cost_minor: 1250, cost_currency: "KWD" }),
    );
  });

  it.each([
    ["24000.50", "INR", 2_400_050],
    ["29.35", "USD", 2935],
    ["150000", "JPY", 150_000],
    ["1.250", "KWD", 1250],
    ["0.005", "KWD", 5],
  ])("sends %s %s as %i minor units", async (typed, currency, minor) => {
    const user = openCreate();
    await fillNewAsset(user, typed, currency);
    await user.click(screen.getByRole("button", { name: "Add asset" }));

    await waitFor(() => expect(client.createAsset).toHaveBeenCalledTimes(1));
    expect(client.createAsset).toHaveBeenCalledWith(
      expect.objectContaining({ tag: "SRV-0042", cost_minor: minor, cost_currency: currency }),
    );
  });

  it("reads the same digits in whichever currency is showing when the form is submitted", async () => {
    const user = openCreate();
    await fillNewAsset(user, "100", "INR");
    const code = screen.getByLabelText(/^Currency/);
    await user.clear(code);
    await user.type(code, "JPY");
    await user.click(screen.getByRole("button", { name: "Add asset" }));

    await waitFor(() => expect(client.createAsset).toHaveBeenCalledTimes(1));
    expect(client.createAsset).toHaveBeenCalledWith(expect.objectContaining({ cost_minor: 100, cost_currency: "JPY" }));
  });
});

describe("AssetFormDialog: refusing a cost", () => {
  it("still refuses a third decimal for rupees, in the words it always used, before any request", async () => {
    const user = openCreate();
    await fillNewAsset(user, "24,000.999", "INR");
    await user.click(screen.getByRole("button", { name: "Add asset" }));

    expect(
      await screen.findByText(
        "Enter an amount in digits, like 24000 or 24000.50, with no currency sign or words and no more than two decimal places.",
      ),
    ).toBeTruthy();
    expect(client.createAsset).not.toHaveBeenCalled();
  });

  it("refuses a decimal for yen, which has none", async () => {
    const user = openCreate();
    await fillNewAsset(user, "1500.50", "JPY");
    await user.click(screen.getByRole("button", { name: "Add asset" }));

    expect(
      await screen.findByText("Enter a whole amount in digits, like 24000. JPY has no decimal places."),
    ).toBeTruthy();
    expect(client.createAsset).not.toHaveBeenCalled();
  });

  it("refuses a fourth decimal for dinars", async () => {
    const user = openCreate();
    await fillNewAsset(user, "1.2505", "KWD");
    await user.click(screen.getByRole("button", { name: "Add asset" }));

    expect(
      await screen.findByText(
        "Enter an amount in digits, like 24000 or 24000.500, with no currency sign or words and no more than three decimal places.",
      ),
    ).toBeTruthy();
    expect(client.createAsset).not.toHaveBeenCalled();
  });
});
