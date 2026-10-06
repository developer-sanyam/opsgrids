import { describe, expect, it } from "vitest";

import { ledgerRowLabel } from "./trackingLabels";

describe("ledgerRowLabel", () => {
  it("calls a storage put-away by name, and leaves the other relocations as they were", () => {
    expect(ledgerRowLabel({ kind: "relocation", reference_type: "putaway" })).toBe("Storage put-away");
    expect(ledgerRowLabel({ kind: "relocation", reference_type: "bin_transfer" })).toBe("Bin transfer");
    expect(ledgerRowLabel({ kind: "relocation", reference_type: "roll_split" })).toBe("Roll split");
    expect(ledgerRowLabel({ kind: "relocation", reference_type: "roll_merge" })).toBe("Roll merge");
    expect(ledgerRowLabel({ kind: "relocation", reference_type: "status_change" })).toBe("Status change");
  });

  it("falls back to a plain relocation for an operation it does not know, and to the kind otherwise", () => {
    expect(ledgerRowLabel({ kind: "relocation", reference_type: "something_new" })).toBe("Relocation");
    expect(ledgerRowLabel({ kind: "relocation", reference_type: null })).toBe("Relocation");
    expect(ledgerRowLabel({ kind: "receipt", reference_type: "putaway" })).toBe("Receipt");
  });
});
