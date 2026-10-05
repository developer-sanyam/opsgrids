/**
 * The warehouse dialog's site position: degrees in the fields, integer micro-degrees on the wire,
 * paired-or-nothing and range checks before anything is sent, an explicit null to clear, and the
 * address lookup as an optional extra — absent without a key, and never able to block a save.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { WarehouseOut } from "@/api/types";
import { resetReports } from "@/lib/clientReport";
import { WarehouseFormDialog } from "./WarehouseFormDialog";

const createWarehouse = vi.fn();
const updateWarehouse = vi.fn();
let lookupThrows = false;

vi.mock("@/api/client", () => ({
  createWarehouse: (...args: unknown[]) => createWarehouse(...args),
  updateWarehouse: (...args: unknown[]) => updateWarehouse(...args),
}));

vi.mock("@/maps/PlacesLookup", () => ({
  default: (props: {
    apiKey: string;
    onPlace: (p: { address: string; lat_micro: number; lng_micro: number }) => void;
    onFailure: (reason: "script_error") => void;
  }) => {
    if (lookupThrows) throw new Error("lookup render error");
    return (
      <div>
        <p>places lookup stub ({props.apiKey})</p>
        <button
          type="button"
          onClick={() => props.onPlace({ address: "Plot 5, MIDC, Bhiwandi, Maharashtra", lat_micro: 19_301_235, lng_micro: 73_061_235 })}
        >
          pick Plot 5
        </button>
        <button type="button" onClick={() => props.onFailure("script_error")}>
          lookup fails
        </button>
      </div>
    );
  },
}));

const PLACED: WarehouseOut = {
  id: "wh-1",
  code: "BHW",
  name: "Bhiwandi DC",
  location: "Bhiwandi",
  lat_micro: 19_300_000,
  lng_micro: 73_060_000,
};
const BARE: WarehouseOut = { id: "wh-2", code: "PUN", name: "Pune Hub", location: "Pune" };

function renderDialog(warehouse: WarehouseOut | null) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<WarehouseFormDialog warehouse={warehouse} onClose={onClose} onSaved={onSaved} />);
  return { onClose, onSaved };
}

describe("WarehouseFormDialog — site position", () => {
  beforeEach(() => {
    resetReports();
    lookupThrows = false;
    createWarehouse.mockReset().mockResolvedValue({ ...BARE });
    updateWarehouse.mockReset().mockResolvedValue({ ...PLACED });
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  describe("the coordinate fields", () => {
    it("use the text keyboard, not the decimal keypad, because iOS's decimal keypad has no minus sign", () => {
      renderDialog(null);
      for (const field of [screen.getByLabelText(/Latitude/), screen.getByLabelText(/Longitude/)]) {
        expect(field.getAttribute("inputmode")).toBe("text");
        expect(field.getAttribute("autocorrect")).toBe("off");
        expect(field.getAttribute("autocapitalize")).toBe("off");
        expect(field.getAttribute("spellcheck")).toBe("false");
      }
    });

    it("carry a pattern that matches exactly what the form accepts, and is valid in the strict v mode", () => {
      renderDialog(null);
      const pattern = screen.getByLabelText(/Latitude/).getAttribute("pattern") ?? "";
      expect(screen.getByLabelText(/Longitude/).getAttribute("pattern")).toBe(pattern);
      const whole = new RegExp(`^(?:${pattern})$`, "v");
      for (const ok of ["19", "19.", "-19.076090", "+5", ".5", "-.5", "−33.8688"]) expect(whole.test(ok), ok).toBe(true);
      for (const bad of ["", "-", "abc", "1e3", "19,07", "19.0.1", "--5", "- 5"]) expect(whole.test(bad), bad).toBe(false);
    });

    it("say which way is negative", () => {
      renderDialog(null);
      expect(screen.getByText("Degrees, between -90 and 90. Use a minus sign for south.")).toBeDefined();
      expect(screen.getByText("Degrees, between -180 and 180. Use a minus sign for west.")).toBeDefined();
    });
  });

  describe("create", () => {
    it("sends a site south and west of 0, which the decimal keypad could not type", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.type(screen.getByLabelText(/^Code/), "SYD");
      await user.type(screen.getByLabelText(/^Name/), "Sydney DC");
      await user.type(screen.getByLabelText(/Latitude/), "-33.8688");
      await user.type(screen.getByLabelText(/Longitude/), "−151.2093");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));

      await waitFor(() =>
        expect(createWarehouse).toHaveBeenCalledWith({
          code: "SYD",
          name: "Sydney DC",
          location: "",
          lat_micro: -33_868_800,
          lng_micro: -151_209_300,
        }),
      );
    });

    it("sends degrees as integer micro-degrees, together", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.type(screen.getByLabelText(/Latitude/), "19.07609");
      await user.type(screen.getByLabelText(/Longitude/), "72.877656");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));

      await waitFor(() =>
        expect(createWarehouse).toHaveBeenCalledWith({
          code: "BHW",
          name: "Bhiwandi DC",
          location: "",
          lat_micro: 19_076_090,
          lng_micro: 72_877_656,
        }),
      );
    });

    it("sends no position at all when both fields are blank", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));

      await waitFor(() => expect(createWarehouse).toHaveBeenCalled());
      const body = createWarehouse.mock.calls[0]?.[0] as Record<string, unknown>;
      expect(body).not.toHaveProperty("lat_micro");
      expect(body).not.toHaveProperty("lng_micro");
    });

    it("refuses one coordinate without the other, on the field that is missing, and sends nothing", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.type(screen.getByLabelText(/Latitude/), "19.07");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));

      expect(await screen.findByText("Enter the longitude too, or clear both fields.")).toBeDefined();
      expect(createWarehouse).not.toHaveBeenCalled();
    });

    it("refuses an out-of-range or non-numeric value on its own field", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.type(screen.getByLabelText(/Latitude/), "95");
      await user.type(screen.getByLabelText(/Longitude/), "abc");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));

      expect(await screen.findByText("Latitude must be between -90 and 90.")).toBeDefined();
      expect(screen.getByText(/Longitude is a number in degrees/)).toBeDefined();
      expect(createWarehouse).not.toHaveBeenCalled();
    });

    it("puts the server's field error on the coordinate field it names", async () => {
      const user = userEvent.setup();
      createWarehouse.mockRejectedValue(
        new ApiError(422, "Invalid", {
          detail: "Invalid",
          errors: [{ field: "lat_micro", code: "range", message: "Latitude is outside the allowed range" }],
        }),
      );
      renderDialog(null);
      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.type(screen.getByLabelText(/Latitude/), "19");
      await user.type(screen.getByLabelText(/Longitude/), "72");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));

      expect(await screen.findByText("Latitude is outside the allowed range")).toBeDefined();
    });
  });

  describe("edit", () => {
    it("shows the stored position in degrees", () => {
      renderDialog(PLACED);
      expect((screen.getByLabelText(/Latitude/) as HTMLInputElement).value).toBe("19.300000");
      expect((screen.getByLabelText(/Longitude/) as HTMLInputElement).value).toBe("73.060000");
    });

    it("leaves the position out of the patch when it did not change", async () => {
      const user = userEvent.setup();
      renderDialog(PLACED);
      await user.clear(screen.getByLabelText(/^Name/));
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi Main DC");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => expect(updateWarehouse).toHaveBeenCalled());
      expect(updateWarehouse.mock.calls[0]).toEqual(["wh-1", { name: "Bhiwandi Main DC", location: "Bhiwandi" }]);
    });

    it("sends both halves when the position changes", async () => {
      const user = userEvent.setup();
      renderDialog(PLACED);
      await user.clear(screen.getByLabelText(/Latitude/));
      await user.type(screen.getByLabelText(/Latitude/), "19.4");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => expect(updateWarehouse).toHaveBeenCalled());
      expect(updateWarehouse.mock.calls[0]?.[1]).toMatchObject({ lat_micro: 19_400_000, lng_micro: 73_060_000 });
    });

    it("clears the position with an explicit null for both when both fields are emptied", async () => {
      const user = userEvent.setup();
      renderDialog(PLACED);
      await user.clear(screen.getByLabelText(/Latitude/));
      await user.clear(screen.getByLabelText(/Longitude/));
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => expect(updateWarehouse).toHaveBeenCalled());
      expect(updateWarehouse.mock.calls[0]?.[1]).toEqual({
        name: "Bhiwandi DC",
        location: "Bhiwandi",
        lat_micro: null,
        lng_micro: null,
      });
    });

    it("does not send a null for a warehouse that never had a position", async () => {
      const user = userEvent.setup();
      renderDialog(BARE);
      await user.clear(screen.getByLabelText(/^Name/));
      await user.type(screen.getByLabelText(/^Name/), "Pune Main");
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      await waitFor(() => expect(updateWarehouse).toHaveBeenCalled());
      expect(updateWarehouse.mock.calls[0]?.[1]).not.toHaveProperty("lat_micro");
    });

    it("treats a partly cleared pair as a mistake, not as a clear", async () => {
      const user = userEvent.setup();
      renderDialog(PLACED);
      await user.clear(screen.getByLabelText(/Longitude/));
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      expect(await screen.findByText("Enter the longitude too, or clear both fields.")).toBeDefined();
      expect(updateWarehouse).not.toHaveBeenCalled();
    });
  });

  describe("without a Maps key", () => {
    it("has no address lookup, says the manual fields are the way in, and still saves", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      expect(screen.queryByText(/places lookup stub/)).toBeNull();
      expect(screen.getByText(/the address lookup isn't set up here/)).toBeDefined();

      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));
      await waitFor(() => expect(createWarehouse).toHaveBeenCalled());
    });
  });

  describe("with a Maps key", () => {
    beforeEach(() => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key");
    });

    it("fills the address and the position from the chosen place, leaving them editable", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.click(await screen.findByRole("button", { name: "pick Plot 5" }));

      expect((screen.getByLabelText(/^Location/) as HTMLInputElement).value).toBe("Plot 5, MIDC, Bhiwandi, Maharashtra");
      expect((screen.getByLabelText(/Latitude/) as HTMLInputElement).value).toBe("19.301235");
      expect((screen.getByLabelText(/Longitude/) as HTMLInputElement).value).toBe("73.061235");

      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));
      await waitFor(() =>
        expect(createWarehouse).toHaveBeenCalledWith({
          code: "BHW",
          name: "Bhiwandi DC",
          location: "Plot 5, MIDC, Bhiwandi, Maharashtra",
          lat_micro: 19_301_235,
          lng_micro: 73_061_235,
        }),
      );
    });

    it("drops the lookup when it fails, says so, reports once, and the manual fields still save", async () => {
      const user = userEvent.setup();
      renderDialog(null);
      await user.click(await screen.findByRole("button", { name: "lookup fails" }));

      expect(screen.queryByText(/places lookup stub/)).toBeNull();
      expect(screen.getByText(/The address lookup isn't available right now/)).toBeDefined();
      expect(console.warn).toHaveBeenCalledWith("[operations] maps_load_failed (script_error)");

      await user.type(screen.getByLabelText(/^Code/), "BHW");
      await user.type(screen.getByLabelText(/^Name/), "Bhiwandi DC");
      await user.type(screen.getByLabelText(/Latitude/), "19");
      await user.type(screen.getByLabelText(/Longitude/), "72");
      await user.click(screen.getByRole("button", { name: "Create warehouse" }));
      await waitFor(() => expect(createWarehouse).toHaveBeenCalled());
    });

    it("drops the lookup when it throws while rendering, and the dialog survives", async () => {
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      lookupThrows = true;
      renderDialog(null);
      expect(await screen.findByText(/The address lookup isn't available right now/)).toBeDefined();
      expect(screen.getByLabelText(/Latitude/)).toBeDefined();
      expect(screen.getByRole("button", { name: "Create warehouse" })).toBeDefined();
    });
  });
});
