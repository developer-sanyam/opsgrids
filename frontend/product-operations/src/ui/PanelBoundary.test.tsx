import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { PanelBoundary } from "./PanelBoundary";

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error("boom with https://maps.googleapis.com/maps/api/js?key=SECRET");
  return <p>panel is fine</p>;
}

function Harness({ onFail }: { onFail?: () => void }) {
  const [explode, setExplode] = useState(true);
  return (
    <div>
      <button type="button" onClick={() => setExplode(false)}>
        fix it
      </button>
      <p>the rest of the page</p>
      <PanelBoundary title="The map" {...(onFail ? { onFail } : {})}>
        <Bomb explode={explode} />
      </PanelBoundary>
    </div>
  );
}

describe("PanelBoundary", () => {
  it("takes down only the panel: the rest of the page stays, and the panel offers a retry", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByRole("alert").textContent).toContain("The map couldn't be shown");
    expect(screen.getByText("the rest of the page")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "fix it" }));
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("panel is fine")).toBeDefined();
  });

  it("tells its owner a failure happened without handing over the error", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onFail = vi.fn();
    render(<Harness onFail={onFail} />);
    expect(onFail).toHaveBeenCalledTimes(1);
    expect(onFail.mock.calls[0]).toEqual([]);
  });

  it("renders a custom fallback in place of the default panel", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <PanelBoundary title="x" fallback={(retry) => <button type="button" onClick={retry}>custom fallback</button>}>
        <Bomb explode />
      </PanelBoundary>,
    );
    expect(screen.getByRole("button", { name: "custom fallback" })).toBeDefined();
  });
});
