/**
 * A chunk that fails to download must be downloadable again. `React.lazy` alone cannot do it: it
 * keeps the rejection, so a retry button that re-renders the same lazy component fails instantly,
 * forever, without the network being asked again.
 */
import { Suspense, lazy } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PanelBoundary } from "./PanelBoundary";
import { retryableLazy } from "./retryableLazy";

function Loaded({ label }: { label: string }) {
  return <p>{label} loaded</p>;
}

describe("retryableLazy", () => {
  it("asks for the chunk again when the panel is retried after a failed download", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    const load = vi
      .fn<() => Promise<{ default: typeof Loaded }>>()
      .mockRejectedValueOnce(new Error("Failed to fetch dynamically imported module"))
      .mockResolvedValueOnce({ default: Loaded });
    const panel = retryableLazy(load);

    render(
      <PanelBoundary title="The scene" onFail={panel.reset}>
        <Suspense fallback={<p>loading</p>}>
          <panel.Panel label="scene" />
        </Suspense>
      </PanelBoundary>,
    );

    expect(await screen.findByText("The scene couldn't be shown")).toBeDefined();
    expect(load).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("scene loaded")).toBeDefined();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("is the reset that makes the difference: a plain lazy component stays failed on retry", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    const load = vi
      .fn<() => Promise<{ default: typeof Loaded }>>()
      .mockRejectedValueOnce(new Error("Failed to fetch dynamically imported module"))
      .mockResolvedValue({ default: Loaded });
    const Plain = lazy(load);

    render(
      <PanelBoundary title="The scene">
        <Suspense fallback={<p>loading</p>}>
          <Plain label="scene" />
        </Suspense>
      </PanelBoundary>,
    );
    await screen.findByText("The scene couldn't be shown");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("The scene couldn't be shown")).toBeDefined();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("does not download anything until the panel is rendered, and only once for a healthy chunk", async () => {
    const load = vi.fn<() => Promise<{ default: typeof Loaded }>>().mockResolvedValue({ default: Loaded });
    const panel = retryableLazy(load);
    panel.reset();
    expect(load).not.toHaveBeenCalled();

    const { rerender } = render(
      <Suspense fallback={<p>loading</p>}>
        <panel.Panel label="scene" />
      </Suspense>,
    );
    expect(await screen.findByText("scene loaded")).toBeDefined();
    rerender(
      <Suspense fallback={<p>loading</p>}>
        <panel.Panel label="scene again" />
      </Suspense>,
    );
    expect(await screen.findByText("scene again loaded")).toBeDefined();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
