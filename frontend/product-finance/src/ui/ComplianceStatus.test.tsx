import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { filingView } from "@/data/compliance";
import { PortalSimulatedNotice } from "./ComplianceStatus";

describe("the GST portal is simulated", () => {
  it("says so on every compliance surface, with nothing to dismiss it", () => {
    render(<PortalSimulatedNotice />);
    expect(screen.getByText("Simulated: not connected to the GST portal")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("a filed document claims no registration with the portal", () => {
    const view = filingView({ required: true, status: "filed", attempts: 1 });
    expect(view?.detail).not.toMatch(/portal/i);
  });
});
