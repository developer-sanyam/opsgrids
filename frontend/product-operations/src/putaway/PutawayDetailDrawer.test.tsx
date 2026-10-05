/**
 * The put-away drawer and, above all, the reveal behind it: the worker's position is shown only to
 * the one role that holds the grant, only after a deliberate click that POSTs (and so is audited on
 * the server), never before, never in the console, and not past the drawer being closed. Every
 * failure — a purged capture, a refusal, a map that will not load — is local to the drawer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { ApiError } from "@opsgrids/client";
import type { PutawayDetailOut, PutawayRevealOut } from "@/api/trackingTypes";
import { resetReports } from "@/lib/clientReport";
import { renderScreen } from "@/test-utils/renderScreen";
import { PutawayDetailDrawer } from "./PutawayDetailDrawer";

const getPutawayDetail = vi.fn();
const revealPutawayLocation = vi.fn();
const listWorkers = vi.fn();
const pinMapProps = vi.fn();
let pinMapThrows = false;

vi.mock("@/api/tracking", () => ({
  getPutawayDetail: (...args: unknown[]) => getPutawayDetail(...args),
  revealPutawayLocation: (...args: unknown[]) => revealPutawayLocation(...args),
}));
vi.mock("@/api/client", () => ({
  listWorkers: (...args: unknown[]) => listWorkers(...args),
}));
vi.mock("@/maps/LocationPinMap", () => ({
  default: (props: {
    apiKey: string;
    lat: number;
    lng: number;
    accuracyM: number | null;
    onFailure: (reason: "script_error" | "auth_failure") => void;
  }) => {
    if (pinMapThrows) throw new Error("map render error");
    pinMapProps(props);
    return (
      <div data-testid="pin-map-stub">
        <button type="button" onClick={() => props.onFailure("auth_failure")}>
          google refuses the key
        </button>
      </div>
    );
  },
}));

const STOCK_READ = "operations.inventory.stock.read:ORG";
const REVEAL = "operations.inventory.putaway.location_read:ORG";
const WORKER_READ = "operations.fulfilment.worker.read:ORG";

const DETAIL: PutawayDetailOut = {
  move_id: "mv-1",
  to_location: { id: "loc-9", code: "R1-B04", name: "Rack 1 bin 4", kind: "bin" },
  worker_id: "worker-1",
  captured_at: "2026-10-05T09:30:00Z",
  matched_suggestion: true,
  has_location: true,
};

const REVEALED: PutawayRevealOut = {
  lat_micro: 19_301_234,
  lng_micro: 73_061_234,
  accuracy_m: 12,
  captured_at: "2026-10-05T09:30:00Z",
};

/** The digits of the position, so a test can prove they appear nowhere they should not. */
const LAT_TEXT = "19.301234";
const LNG_TEXT = "73.061234";

function Harness({ initial = "mv-1" }: { initial?: string | null }) {
  const [moveId, setMoveId] = useState<string | null>(initial);
  return (
    <div>
      <button type="button" onClick={() => setMoveId("mv-1")}>
        open drawer
      </button>
      <PutawayDetailDrawer moveId={moveId} onClose={() => setMoveId(null)} />
    </div>
  );
}

function renderDrawer(grants: string[] = [STOCK_READ, REVEAL], initial: string | null = "mv-1") {
  return renderScreen(<Harness initial={initial} />, grants);
}

function consoleOutput(spies: ReturnType<typeof vi.spyOn>[]): string {
  return JSON.stringify(spies.flatMap((spy) => spy.mock.calls));
}

describe("PutawayDetailDrawer", () => {
  beforeEach(() => {
    resetReports();
    pinMapThrows = false;
    getPutawayDetail.mockReset().mockResolvedValue(DETAIL);
    revealPutawayLocation.mockReset().mockResolvedValue(REVEALED);
    listWorkers.mockReset().mockResolvedValue({
      items: [{ id: "worker-1", display_name: "Ravi Kumar" }],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    pinMapProps.mockReset();
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  describe("the facts", () => {
    it("shows the bin, who stored it, when and whether it matched the suggestion — and no coordinates", async () => {
      renderDrawer([STOCK_READ, WORKER_READ]);
      const drawer = await screen.findByRole("dialog", { name: "Put-away details" });
      expect(await within(drawer).findByText("R1-B04")).toBeDefined();
      expect(within(drawer).getByText(/Rack 1 bin 4/)).toBeDefined();
      expect(await within(drawer).findByText("Ravi Kumar")).toBeDefined();
      expect(within(drawer).getByText("Matched the suggestion").nextElementSibling?.textContent).toBe("Yes");
      expect(within(drawer).getByText("Position captured").nextElementSibling?.textContent).toBe("Yes");
      expect(drawer.textContent).not.toContain(LAT_TEXT);
      expect(drawer.textContent).not.toContain(LNG_TEXT);
      expect(getPutawayDetail).toHaveBeenCalledWith("mv-1");
    });

    it("says a manager with no worker profile did it, and does not guess a name", async () => {
      getPutawayDetail.mockResolvedValue({ ...DETAIL, worker_id: null });
      renderDrawer();
      expect(await screen.findByText("A manager (no worker profile)")).toBeDefined();
      expect(listWorkers).not.toHaveBeenCalled();
    });

    it("uses a plain phrase rather than an id when the caller cannot read workers", async () => {
      renderDrawer([STOCK_READ]);
      expect(await screen.findByText("A warehouse worker")).toBeDefined();
      expect(listWorkers).not.toHaveBeenCalled();
    });

    it("says no bin was suggested, or that the scanned bin differed", async () => {
      getPutawayDetail.mockResolvedValue({ ...DETAIL, matched_suggestion: null });
      const { unmount } = renderDrawer();
      expect(await screen.findByText("No bin was suggested")).toBeDefined();
      unmount();
      getPutawayDetail.mockResolvedValue({ ...DETAIL, matched_suggestion: false });
      renderDrawer();
      await screen.findByText("Matched the suggestion");
      expect(screen.getByText("Matched the suggestion").nextElementSibling?.textContent).toBe("No");
    });

    it("holds the drawer's place with a skeleton, and says so after 8 seconds", async () => {
      vi.useFakeTimers();
      try {
        getPutawayDetail.mockReturnValue(new Promise(() => undefined));
        renderDrawer();
        expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
        await act(async () => {
          vi.advanceTimersByTime(8000);
        });
        expect(screen.getAllByRole("status").some((s) => s.textContent?.includes("taking longer than usual"))).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    });

    it("says a purged or missing capture is gone (404), rather than showing an error", async () => {
      getPutawayDetail.mockRejectedValue(new ApiError(404, "No capture", { detail: "No capture" }));
      renderDrawer();
      expect(await screen.findByText("No capture on record")).toBeDefined();
      expect(screen.getByText(/kept for 30 days/)).toBeDefined();
    });

    it("shows any other failure with a retry that asks again", async () => {
      const user = userEvent.setup();
      getPutawayDetail.mockRejectedValueOnce(new ApiError(500, "Ledger unavailable", { detail: "Ledger unavailable" }));
      renderDrawer();
      expect(await screen.findByText("Ledger unavailable")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByText("R1-B04")).toBeDefined();
    });

    it("stays closed, and asks for nothing, until a move is chosen", () => {
      renderDrawer([STOCK_READ, REVEAL], null);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(getPutawayDetail).not.toHaveBeenCalled();
    });
  });

  describe("who may reveal", () => {
    it("offers no way to show the location to anyone without the putaway.location_read grant", async () => {
      renderDrawer([STOCK_READ, WORKER_READ]);
      await screen.findByText("R1-B04");
      expect(screen.queryByRole("button", { name: "Show worker location" })).toBeNull();
      expect(screen.queryByText("Worker location")).toBeNull();
      expect(revealPutawayLocation).not.toHaveBeenCalled();
    });

    it("offers it, with the audit warning beside it, to someone who holds the grant", async () => {
      renderDrawer();
      expect(await screen.findByRole("button", { name: "Show worker location" })).toBeDefined();
      expect(screen.getByText(/recorded in the audit log, every time/)).toBeDefined();
      expect(revealPutawayLocation).not.toHaveBeenCalled();
    });

    it("says there is nothing to show when no position was captured, and offers no button", async () => {
      getPutawayDetail.mockResolvedValue({ ...DETAIL, has_location: false });
      renderDrawer();
      expect(await screen.findByText("No position was captured")).toBeDefined();
      expect(screen.queryByRole("button", { name: "Show worker location" })).toBeNull();
    });
  });

  describe("the reveal", () => {
    it("shows no coordinates before the click, then POSTs once and shows them as text", async () => {
      const user = userEvent.setup();
      renderDrawer();
      const button = await screen.findByRole("button", { name: "Show worker location" });
      expect(document.body.textContent).not.toContain(LAT_TEXT);

      await user.click(button);

      expect(await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`)).toBeDefined();
      expect(revealPutawayLocation).toHaveBeenCalledTimes(1);
      expect(revealPutawayLocation).toHaveBeenCalledWith("mv-1");
      expect(screen.queryByRole("button", { name: "Show worker location" })).toBeNull();
    });

    it("draws a pin and an accuracy circle's worth of props on the map when there is a key", async () => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key");
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));

      expect(await screen.findByTestId("pin-map-stub")).toBeDefined();
      expect(pinMapProps.mock.calls.at(-1)?.[0]).toMatchObject({
        apiKey: "test-key",
        lat: 19.301234,
        lng: 73.061234,
        accuracyM: 12,
      });
      // The text is there too, for anyone the map does not serve.
      expect(screen.getByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`)).toBeDefined();
      expect(screen.getByText(/The circle is the device's reported accuracy/)).toBeDefined();
    });

    it("falls back to coordinates as text, and says why, with no key", async () => {
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText(/The map isn't available, so the position is shown as coordinates/)).toBeDefined();
      expect(screen.queryByTestId("pin-map-stub")).toBeNull();
    });

    it("falls back to the text when the map refuses the key or throws", async () => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key");
      vi.spyOn(console, "warn").mockImplementation(() => undefined);
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      await user.click(await screen.findByRole("button", { name: "google refuses the key" }));

      expect(await screen.findByText(/The map isn't available/)).toBeDefined();
      expect(screen.getByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`)).toBeDefined();
      expect(console.warn).toHaveBeenCalledWith("[operations] maps_load_failed (auth_failure)");
    });

    it("falls back the same way when the map throws while rendering", async () => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key");
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      vi.spyOn(console, "warn").mockImplementation(() => undefined);
      pinMapThrows = true;
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText(/The map isn't available/)).toBeDefined();
      expect(screen.getByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`)).toBeDefined();
    });

    it("shows a position without an accuracy figure as plain coordinates", async () => {
      revealPutawayLocation.mockResolvedValue({ ...REVEALED, accuracy_m: null });
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT}`)).toBeDefined();
    });

    it("says no usable position when the record answers with nulls, or with an impossible one", async () => {
      const user = userEvent.setup();
      revealPutawayLocation.mockResolvedValue({ lat_micro: null, lng_micro: null, accuracy_m: null, captured_at: REVEALED.captured_at });
      const { unmount } = renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText("The record holds no usable position for this put-away.")).toBeDefined();
      unmount();

      revealPutawayLocation.mockResolvedValue({ ...REVEALED, lat_micro: 99_000_000 });
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText("The record holds no usable position for this put-away.")).toBeDefined();
      expect(document.body.textContent).not.toContain("99.000000");
    });

    it("hides the position again on request, and a second reveal is a second POST", async () => {
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`);

      await user.click(screen.getByRole("button", { name: "Hide location" }));
      expect(document.body.textContent).not.toContain(LAT_TEXT);

      await user.click(screen.getByRole("button", { name: "Show worker location" }));
      await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`);
      expect(revealPutawayLocation).toHaveBeenCalledTimes(2);
    });

    it("does not keep the position past the drawer: closing and reopening needs another reveal", async () => {
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`);

      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(document.body.textContent).not.toContain(LAT_TEXT);

      await user.click(screen.getByRole("button", { name: "open drawer" }));
      expect(await screen.findByRole("button", { name: "Show worker location" })).toBeDefined();
      expect(document.body.textContent).not.toContain(LAT_TEXT);
    });

    it("says the capture expired when the server answers 404, and shows nothing", async () => {
      revealPutawayLocation.mockRejectedValue(new ApiError(404, "gone", { detail: "gone" }));
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      const alert = await screen.findByText("Capture expired");
      expect(alert).toBeDefined();
      expect(screen.getByText(/30-day retention window/)).toBeDefined();
      expect(screen.getByRole("button", { name: "Show worker location" })).toBeDefined();
    });

    it("shows a refusal (403) in words, with no coordinates, and lets the user try again", async () => {
      revealPutawayLocation.mockRejectedValueOnce(new ApiError(403, "You may not read worker locations", { detail: "You may not read worker locations" }));
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText("Couldn't show the location")).toBeDefined();
      expect(screen.getByText("You may not read worker locations")).toBeDefined();
      expect(document.body.textContent).not.toContain(LAT_TEXT);

      await user.click(screen.getByRole("button", { name: "Show worker location" }));
      expect(await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`)).toBeDefined();
    });

    it("never writes the position to the console, storage or the address", async () => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key");
      const spies = [
        vi.spyOn(console, "log").mockImplementation(() => undefined),
        vi.spyOn(console, "info").mockImplementation(() => undefined),
        vi.spyOn(console, "warn").mockImplementation(() => undefined),
        vi.spyOn(console, "error").mockImplementation(() => undefined),
        vi.spyOn(console, "debug").mockImplementation(() => undefined),
      ];
      const user = userEvent.setup();
      renderDrawer();
      await user.click(await screen.findByRole("button", { name: "Show worker location" }));
      await screen.findByText(`${LAT_TEXT}, ${LNG_TEXT} (within 12 m)`);
      await user.click(await screen.findByRole("button", { name: "google refuses the key" }));
      await user.click(screen.getByRole("button", { name: "Hide location" }));

      const logged = consoleOutput(spies);
      expect(logged).not.toContain("19.30");
      expect(logged).not.toContain("73.06");
      expect(logged).not.toContain("19301234");
      expect(logged).not.toContain("73061234");
      expect(logged).not.toContain("test-key");
      expect(window.location.href).not.toContain("19.30");
      expect(JSON.stringify({ ...window.localStorage })).not.toContain("19.30");
      expect(JSON.stringify({ ...window.sessionStorage })).not.toContain("19.30");
    });
  });
});
