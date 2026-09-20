import { describe, expect, it } from "vitest";

import { shortProductName } from "./productName";

describe("shortProductName", () => {
  it("drops the brand from a catalogue name", () => {
    expect(shortProductName("OpsGrids People")).toBe("People");
    expect(shortProductName("OpsGrids IT")).toBe("IT");
    expect(shortProductName("OpsGrids   Inventory")).toBe("Inventory");
  });

  it("leaves a name without the brand alone", () => {
    expect(shortProductName("PulseBoard")).toBe("PulseBoard");
    expect(shortProductName("My OpsGrids Add-on")).toBe("My OpsGrids Add-on");
  });

  it("never returns an empty name", () => {
    expect(shortProductName("OpsGrids ")).toBe("OpsGrids ");
    expect(shortProductName("OpsGrids")).toBe("OpsGrids");
  });
});
