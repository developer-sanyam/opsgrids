/**
 * The storage put-away screen end to end, through the real api client, the real queue and the real
 * drainer — only the network (`stubGateway`) and the phone's sensors (`stubGeolocation`) are
 * faked, so what these assert on is what crosses the wire and what a worker sees.
 *
 * The camera is never available here (jsdom has no `BarcodeDetector`), which is the same "no
 * camera" fallback a denied-permission phone gets: the manual field is what every test drives. The
 * camera path has its own test (`PutawayBinScan.test.tsx`).
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useNavigate } from "react-router-dom";

import type { PutawayIn } from "@/api/types";
import { PutawayQueueProvider } from "@/lib/PutawayQueueProvider";
import { enqueuePutaway, getPutawayQueue, parkPutaway, purgePutawayQueue } from "@/lib/putawayQueue";
import {
  NOW,
  json,
  makePutawayOut,
  makeStockRow,
  makeSuggestion,
  problem,
  removeGeolocation,
  renderScreen,
  stubGateway,
  stubGeolocation,
} from "@/worker/testKit";
import type { Answer, Call } from "@/worker/testKit";

import { PutawayPage } from "./Putaway";

const STOCK = "GET /api/v1/operations/inventory/stock";
const SUGGEST = "GET /api/v1/operations/inventory/putaway/suggest";
const PUTAWAY = "POST /api/v1/operations/inventory/putaway";
const GRANTS = ["operations.inventory.stock.read:ORG", "operations.inventory.stock.transfer:ORG"];
const STORAGE_KEY = "opsgrids.worker.putawayQueue";

function renderPutaway(grants: string[] = GRANTS) {
  return renderScreen(
    <PutawayQueueProvider ownerSub="user-1" organizationId="org-1">
      <PutawayPage />
    </PutawayQueueProvider>,
    { at: "/putaway", path: "/putaway", grants },
  );
}

function gateway(overrides: Record<string, Answer> = {}) {
  return stubGateway({
    [STOCK]: () => json({ items: [makeStockRow()], next_cursor: null }),
    [SUGGEST]: () => json({ suggestion: makeSuggestion() }),
    [PUTAWAY]: () => json(makePutawayOut(), 201),
    ...overrides,
  });
}

const posts = (calls: Call[]) => calls.filter((call) => call.key === PUTAWAY);

async function pickStock(user: UserEvent) {
  await user.click(await screen.findByRole("radio", { name: /SKU-100/ }));
}

async function enterBin(user: UserEvent, label: string) {
  await user.type(await screen.findByLabelText("Bin label"), label);
  await user.click(screen.getByRole("button", { name: "Use this label" }));
}

async function confirm(user: UserEvent, name: "Put away" | "Put away here anyway" = "Put away") {
  await user.click(await screen.findByRole("button", { name }));
}

function setOnline(online: boolean) {
  vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(online);
}

afterEach(() => {
  vi.unstubAllGlobals();
  removeGeolocation();
  purgePutawayQueue();
  localStorage.clear();
});

describe("PutawayPage", () => {
  describe("the happy path", () => {
    it("sends one request: the frozen body, an Idempotency-Key and integer micro-degrees — then says where it went", async () => {
      stubGeolocation((ok) => ok({ coords: { latitude: 12.9715987, longitude: 77.5945627, accuracy: 12.4 } }));
      const { calls } = gateway({
        [STOCK]: () => json({ items: [makeStockRow({ lot_id: "lot-1", lot_no: "LOT-7" })], next_cursor: null }),
      });
      const user = userEvent.setup();
      renderPutaway();

      await pickStock(user);
      const quantity = await screen.findByLabelText(/Quantity to put away/);
      expect((quantity as HTMLInputElement).value).toBe("20");
      await user.clear(quantity);
      await user.type(quantity, "5");
      expect(await screen.findByText("A-01-03")).toBeTruthy(); // the suggested bin
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText("Put away recorded")).toBeTruthy();
      expect(screen.getByText("5 m of SKU-100 is in A-01-03 (Rack A, bay 1, level 3).")).toBeTruthy();

      const [sent] = posts(calls);
      expect(posts(calls)).toHaveLength(1);
      expect(sent?.body).toEqual({
        inventory_item_id: "item-1",
        from_location_id: "loc-rcv-1",
        to_barcode: "A-01-03",
        quantity: "5",
        lot_id: "lot-1",
        suggested_location_id: "loc-bin-1",
        // `NOW` is pinned in test-setup.ts, so the confirm moment is exact. No `occurred_on`: online,
        // the server's own date applies (see "a phone whose clock is a day ahead" below).
        captured_at: NOW,
        lat_micro: 12_971_599,
        lng_micro: 77_594_563,
        accuracy_m: 12,
      });
      expect(sent?.headers["idempotency-key"]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      expect(sent?.headers.authorization === undefined || typeof sent.headers.authorization === "string").toBe(true);
      // The suggestion lookup used the bucket's own item, warehouse and lot.
      const suggest = calls.find((call) => call.key === SUGGEST);
      expect(Object.fromEntries(suggest?.search ?? [])).toEqual({
        inventory_item_id: "item-1",
        warehouse_id: "warehouse-1",
        lot_id: "lot-1",
      });
      // Synced: nothing is left on the phone, coordinates included.
      expect(getPutawayQueue()).toEqual([]);
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it("asks for AVAILABLE stock only", async () => {
      const { calls } = gateway();
      renderPutaway();

      await screen.findByRole("radio", { name: /SKU-100/ });

      const stock = calls.find((call) => call.key === STOCK);
      expect(stock?.search.get("status")).toBe("AVAILABLE");
    });

    it("a put-away saved offline freezes the UTC date, even when the phone's own date is a day behind", async () => {
      process.env.TZ = "America/Los_Angeles";
      try {
        vi.setSystemTime(new Date("2026-09-30T00:30:00.000Z")); // 5:30 pm on the 29th in Los Angeles
        setOnline(false);
        gateway();
        const user = userEvent.setup();
        renderPutaway();

        await pickStock(user);
        await enterBin(user, "A-01-03");
        await confirm(user);
        await screen.findByText("Saved on this phone");

        expect(new Date().getDate()).toBe(29);
        expect(getPutawayQueue()[0]?.body).toMatchObject({
          captured_at: "2026-09-30T00:30:00.000Z",
          occurred_on: "2026-09-30",
        });
      } finally {
        delete process.env.TZ;
      }
    });

    it("a phone whose clock is a day ahead is not refused: online, no date is sent and the server's own applies", async () => {
      // The server rejects an `occurred_on` after its own today; the phone's clock is the only thing
      // that could make one, so the phone does not send it while the server is there to ask.
      vi.setSystemTime(new Date("2026-09-30T09:00:00.000Z"));
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();

      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      await screen.findByText("Put away recorded");
      const body = posts(calls)[0]?.body as PutawayIn;
      expect(body).not.toHaveProperty("occurred_on");
      expect(body.captured_at).toBe("2026-09-30T09:00:00.000Z");
    });

    it("trims the CR/LF a scanner adds, so the label sent is the label printed", async () => {
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();

      await pickStock(user);
      const field = await screen.findByLabelText("Bin label");
      await user.type(field, "  A-01-03  {Enter}");

      expect(await screen.findByRole("button", { name: "Change bin label" })).toBeTruthy();
      await confirm(user);
      await screen.findByText("Put away recorded");
      expect(posts(calls)[0]?.body).toMatchObject({ to_barcode: "A-01-03" });
    });

    it("can start another put-away from the result, with fresh stock", async () => {
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);
      await user.click(await screen.findByRole("button", { name: "Put away more" }));

      expect(await screen.findByRole("radio", { name: /SKU-100/ })).toBeTruthy();
      expect(calls.filter((call) => call.key === STOCK)).toHaveLength(2);
    });

    it("a replay of a key the server already holds is reported as already recorded", async () => {
      gateway({ [PUTAWAY]: () => json(makePutawayOut({ replayed: true }), 200) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText("Already recorded")).toBeTruthy();
    });

    it("one tap is one put-away, however fast the second comes", async () => {
      let release: (() => void) | undefined;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      const { calls } = gateway({
        [PUTAWAY]: async () => {
          await gate;
          return json(makePutawayOut(), 201);
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");

      const button = await screen.findByRole("button", { name: "Put away" });
      await user.dblClick(button);
      release?.();

      await screen.findByText("Put away recorded");
      expect(posts(calls)).toHaveLength(1);
    });
  });

  describe("the suggested bin", () => {
    it("a different bin than suggested gets an amber warning and an explicit confirm before anything is sent", async () => {
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "B-02-01");

      expect(await screen.findByText("That isn't the suggested bin")).toBeTruthy();
      expect(screen.getByText(/The system suggested A-01-03/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Put away" })).toBeNull();
      expect(posts(calls)).toHaveLength(0);

      await confirm(user, "Put away here anyway");

      await screen.findByText("Put away recorded");
      expect(posts(calls)[0]?.body).toMatchObject({ to_barcode: "B-02-01", suggested_location_id: "loc-bin-1" });
    });

    it("scanning the suggested bin, in any case, raises no warning", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "a-01-03");

      expect(await screen.findByRole("button", { name: "Put away" })).toBeTruthy();
      expect(screen.queryByText("That isn't the suggested bin")).toBeNull();
    });

    it("scanning the suggested bin's printed barcode, which is not its code, raises no warning either", async () => {
      const { calls } = gateway({ [SUGGEST]: () => json({ suggestion: makeSuggestion({ barcode: "LBL-A0103" }) }) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      // The card says what the label reads, since that — not the code — is what gets scanned.
      expect(await screen.findByText("LBL-A0103")).toBeTruthy();
      await enterBin(user, "lbl-a0103");

      const put = await screen.findByRole("button", { name: "Put away" });
      expect(screen.queryByText("That isn't the suggested bin")).toBeNull();

      await user.click(put);
      await screen.findByText("Put away recorded");
      expect(posts(calls)[0]?.body).toMatchObject({ to_barcode: "lbl-a0103", suggested_location_id: "loc-bin-1" });
    });

    it("typing the suggested bin's code, when its label reads something else, is not a match: the server resolves labels only", async () => {
      gateway({ [SUGGEST]: () => json({ suggestion: makeSuggestion({ barcode: "LBL-A0103" }) }) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");

      expect(await screen.findByText("That isn't the suggested bin")).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Put away" })).toBeNull();
    });

    it("a suggested bin with no barcode says it can't be scanned, and typing its code is not a match", async () => {
      gateway({ [SUGGEST]: () => json({ suggestion: makeSuggestion({ barcode: null }) }) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);

      expect(await screen.findByText("This bin has no scannable label")).toBeTruthy();
      await enterBin(user, "A-01-03");

      expect(await screen.findByText("That isn't the suggested bin")).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Put away" })).toBeNull();
    });

    it("a labelled suggested bin does not claim to be unlabelled", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);

      expect(await screen.findByText("Suggested bin")).toBeTruthy();
      expect(screen.queryByText("This bin has no scannable label")).toBeNull();
    });

    it("scanning again clears the warning along with the old label", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "B-02-01");
      await screen.findByText("That isn't the suggested bin");

      await user.click(screen.getByRole("button", { name: "Change bin label" }));

      expect(screen.queryByText("That isn't the suggested bin")).toBeNull();
      expect(await screen.findByLabelText("Bin label")).toBeTruthy();
    });

    it("a server note that it wasn't the suggested bin is shown calmly on the result", async () => {
      gateway({ [PUTAWAY]: () => json(makePutawayOut({ matched_suggestion: false }), 201) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "B-02-01");
      await confirm(user, "Put away here anyway");

      expect(await screen.findByText("That was not the suggested bin.")).toBeTruthy();
    });

    it("no suggestion is said plainly and the worker carries on", async () => {
      const { calls } = gateway({ [SUGGEST]: () => json({ suggestion: null }) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);

      expect(await screen.findByText(/No bin is suggested for this stock/)).toBeTruthy();
      await enterBin(user, "C-09-09");
      await confirm(user);

      await screen.findByText("Put away recorded");
      expect(posts(calls)[0]?.body).not.toHaveProperty("suggested_location_id");
    });

    it("a suggestion that fails to load is not a blocker, and can be retried", async () => {
      let attempts = 0;
      const { calls } = gateway({
        [SUGGEST]: () => {
          attempts += 1;
          return attempts === 1 ? problem(500, "Suggestions are down") : json({ suggestion: makeSuggestion() });
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);

      expect(await screen.findByText("Couldn't load a bin suggestion")).toBeTruthy();
      await enterBin(user, "C-09-09");
      await confirm(user);
      await screen.findByText("Put away recorded");
      expect(posts(calls)[0]?.body).not.toHaveProperty("suggested_location_id");
    });

    it("Try again fetches the suggestion again", async () => {
      let attempts = 0;
      gateway({
        [SUGGEST]: () => {
          attempts += 1;
          return attempts === 1 ? problem(500, "down") : json({ suggestion: makeSuggestion() });
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await user.click(await screen.findByRole("button", { name: "Try again" }));

      expect(await screen.findByText("Suggested bin")).toBeTruthy();
      expect(screen.getByText("A-01-03")).toBeTruthy();
    });
  });

  describe("quantity", () => {
    it.each([
      ["25", "Only 20 m is available there."],
      ["0", "Enter more than zero."],
      ["", "Enter how much you're putting away."],
      ["1.2345", "Enter a number with up to 3 decimals."],
    ])("%j is refused on the field and nothing is sent", async (typed, message) => {
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      const field = await screen.findByLabelText(/Quantity to put away/);
      await user.clear(field);
      if (typed) await user.type(field, typed);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText(message)).toBeTruthy();
      expect(posts(calls)).toHaveLength(0);
    });
  });

  describe("the phone's location", () => {
    it.each([
      ["denied", (_ok: unknown, fail: (e: { code: number }) => void) => fail({ code: 1 }), /Location is turned off/],
      ["slow", (_ok: unknown, fail: (e: { code: number }) => void) => fail({ code: 3 }), /in time/],
      ["unavailable", (_ok: unknown, fail: (e: { code: number }) => void) => fail({ code: 2 }), /wasn't available/],
    ])("%s: the put-away still goes through, without a position, with a calm notice", async (_name, behave, notice) => {
      stubGeolocation((ok, fail) => behave(ok, fail));
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText("Put away recorded")).toBeTruthy();
      expect(screen.getByText(notice)).toBeTruthy();
      const body = posts(calls)[0]?.body as PutawayIn;
      expect(body).not.toHaveProperty("lat_micro");
      expect(body).not.toHaveProperty("lng_micro");
      expect(body).not.toHaveProperty("accuracy_m");
    });

    it("unsupported: no geolocation on this phone at all", async () => {
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText(/can't share its location/)).toBeTruthy();
      expect(posts(calls)[0]?.body).not.toHaveProperty("lat_micro");
    });

    it("a reading that is not finite is left out", async () => {
      stubGeolocation((ok) => ok({ coords: { latitude: Number.NaN, longitude: 77.5, accuracy: 5 } }));
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText(/reading wasn't usable/)).toBeTruthy();
      const body = posts(calls)[0]?.body as PutawayIn;
      expect(body).not.toHaveProperty("lat_micro");
      expect(body).not.toHaveProperty("lng_micro");
    });

    it("asks the phone once, for a high-accuracy fix with a five second timeout", async () => {
      const getCurrentPosition = stubGeolocation((ok) =>
        ok({ coords: { latitude: 1, longitude: 2, accuracy: 3 } }),
      );
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);
      await screen.findByText("Put away recorded");

      expect(getCurrentPosition).toHaveBeenCalledTimes(1);
      expect(getCurrentPosition.mock.calls[0]?.[2]).toEqual({ enableHighAccuracy: true, timeout: 5000, maximumAge: 0 });
    });

    it("the server's own notes about the position or the clock are shown, and none of them is an error", async () => {
      gateway({
        [PUTAWAY]: () =>
          json(
            makePutawayOut({
              geo_recorded: false,
              warnings: ["geo_dropped:out_of_range", "captured_at_adjusted", "geo_not_recorded", "a_future_code"],
            }),
            201,
          ),
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText(/couldn't use your location reading/)).toBeTruthy();
      expect(screen.getByText(/phone's clock looks off/)).toBeTruthy();
      expect(screen.getByText(/location couldn't be saved/)).toBeTruthy();
      expect(screen.queryByText(/a_future_code/)).toBeNull();
      expect(screen.getByText("Put away recorded")).toBeTruthy();
    });
  });

  describe("offline and queued", () => {
    it("offline: no position is requested, the put-away is saved on the phone, and replays under the same key once online", async () => {
      const getCurrentPosition = stubGeolocation((ok) => ok({ coords: { latitude: 1, longitude: 2, accuracy: 3 } }));
      const { calls } = gateway();
      setOnline(false);
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText("Saved on this phone")).toBeTruthy();
      expect(screen.getByText(/You're offline\. It will sync automatically/)).toBeTruthy();
      expect(screen.getByText(/saved without your location/)).toBeTruthy();
      expect(screen.getByText("1 put-away waiting to sync")).toBeTruthy();
      expect(getCurrentPosition).not.toHaveBeenCalled();
      expect(posts(calls)).toHaveLength(0);
      const [queued] = getPutawayQueue();
      expect(queued).toMatchObject({ ownerSub: "user-1", organizationId: "org-1", status: "pending" });
      expect(queued?.body).toMatchObject({ captured_at: NOW, occurred_on: "2026-09-29", to_barcode: "A-01-03" });

      setOnline(true);
      window.dispatchEvent(new Event("online"));

      await waitFor(() => expect(posts(calls)).toHaveLength(1));
      expect(posts(calls)[0]?.headers["idempotency-key"]).toBe(queued?.idempotencyKey);
      expect(posts(calls)[0]?.body).toEqual(queued?.body);
      await waitFor(() => expect(screen.queryByText(/waiting to sync/)).toBeNull());
      expect(getPutawayQueue()).toEqual([]);
    });

    it("a server error saves it for later; Sync now resends the very same request", async () => {
      let attempt = 0;
      const { calls } = gateway({
        [PUTAWAY]: () => {
          attempt += 1;
          return attempt === 1 ? problem(503, "Service unavailable") : json(makePutawayOut(), 201);
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText("Saved on this phone")).toBeTruthy();
      expect(screen.getByText(/The server had a problem/)).toBeTruthy();
      expect(await screen.findByText("1 put-away waiting to sync")).toBeTruthy();

      await user.click(screen.getByRole("button", { name: "Sync now" }));

      await waitFor(() => expect(posts(calls)).toHaveLength(2));
      expect(posts(calls)[1]?.headers["idempotency-key"]).toBe(posts(calls)[0]?.headers["idempotency-key"]);
      expect(posts(calls)[1]?.body).toEqual(posts(calls)[0]?.body);
      await waitFor(() => expect(getPutawayQueue()).toEqual([]));
    });

    it("a dropped connection mid-request is queued, not lost", async () => {
      let attempt = 0;
      const { calls } = gateway({
        [PUTAWAY]: () => {
          attempt += 1;
          if (attempt === 1) throw new TypeError("Failed to fetch");
          return json(makePutawayOut(), 201);
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText(/Couldn't reach the server/)).toBeTruthy();
      expect(getPutawayQueue()).toHaveLength(1);
      await user.click(await screen.findByRole("button", { name: "Sync now" }));
      await waitFor(() => expect(posts(calls)).toHaveLength(2));
    });

    async function putAwayFive(user: UserEvent) {
      await pickStock(user);
      const quantity = await screen.findByLabelText(/Quantity to put away/);
      await user.clear(quantity);
      await user.type(quantity, "5");
      await enterBin(user, "A-01-03");
      await confirm(user);
    }

    it("offline, Put away more keeps the list that was already loaded — less what is waiting to sync — so the next line can be entered", async () => {
      setOnline(false);
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await putAwayFive(user);
      await screen.findByText("Saved on this phone");

      await user.click(screen.getByRole("button", { name: "Put away more" }));

      expect(await screen.findByRole("radio", { name: /SKU-100/ })).toBeTruthy();
      expect(screen.getByText(/15 m available/)).toBeTruthy();
      expect(screen.queryByText("Couldn't load stock")).toBeNull();
      expect(calls.filter((call) => call.key === STOCK)).toHaveLength(1);
    });

    it("when the list cannot be refreshed, Put away more offers the one it has, says so, and Refresh asks again", async () => {
      let lists = 0;
      gateway({
        [PUTAWAY]: () => problem(503, "Service unavailable"),
        [STOCK]: () => {
          lists += 1;
          if (lists === 2) throw new TypeError("Failed to fetch");
          return json({ items: [makeStockRow()], next_cursor: null });
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await putAwayFive(user);
      await screen.findByText("Saved on this phone");

      await user.click(screen.getByRole("button", { name: "Put away more" }));

      expect(await screen.findByText("Couldn't refresh the stock list")).toBeTruthy();
      expect(screen.getByRole("radio", { name: /SKU-100/ })).toBeTruthy();
      expect(screen.getByText(/15 m available/)).toBeTruthy();

      await user.click(screen.getByRole("button", { name: "Refresh" }));

      await waitFor(() => expect(screen.queryByText("Couldn't refresh the stock list")).toBeNull());
      expect(screen.getByRole("radio", { name: /SKU-100/ })).toBeTruthy();
    });

    it("stock already waiting to sync is not offered at full quantity again, even when the list loads fine", async () => {
      gateway({ [PUTAWAY]: () => problem(503, "Service unavailable") });
      const user = userEvent.setup();
      renderPutaway();
      await putAwayFive(user);
      await screen.findByText("Saved on this phone");

      await user.click(screen.getByRole("button", { name: "Put away more" }));

      expect(await screen.findByText(/15 m available/)).toBeTruthy();
      expect(screen.queryByText(/20 m available/)).toBeNull();
    });

    it("a put-away the server refused and parked holds nothing back: it will never be sent", async () => {
      const refused = enqueuePutaway({
        ownerSub: "user-1",
        organizationId: "org-1",
        body: { inventory_item_id: "item-1", from_location_id: "loc-rcv-1", to_barcode: "A-01-03", quantity: "5" },
        summary: { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" },
      });
      parkPutaway(refused.idempotencyKey, { httpStatus: 409, message: "That stock is reserved." });
      gateway();
      renderPutaway();

      expect(await screen.findByText(/20 m available/)).toBeTruthy();
    });

    it("with localStorage unavailable the queue still works for the session — and says it can't promise more", async () => {
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("quota", "QuotaExceededError");
      });
      const { calls } = gateway();
      setOnline(false);
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText("Saved on this phone")).toBeTruthy();
      expect(await screen.findByText("This phone can't save put-aways while offline")).toBeTruthy();
      expect(screen.getByText("1 put-away waiting to sync")).toBeTruthy();
      // The reassurance a persistent queue gives would be a lie here.
      expect(screen.getByText(/Held in this app only/)).toBeTruthy();
      expect(screen.queryByText(/Saved on this phone\. They sync whenever/)).toBeNull();

      setOnline(true);
      window.dispatchEvent(new Event("online"));
      await waitFor(() => expect(posts(calls)).toHaveLength(1));
      await waitFor(() => expect(screen.queryByText(/waiting to sync/)).toBeNull());
    });

    it("another account's saved put-aways are neither shown nor sent", async () => {
      enqueuePutaway({
        ownerSub: "someone-else",
        organizationId: "org-1",
        body: {
          inventory_item_id: "item-9",
          from_location_id: "loc-9",
          to_barcode: "Z-99",
          quantity: "1",
          lat_micro: 1,
          lng_micro: 2,
        },
        summary: { sku: "SKU-9", itemName: "Other", uom: "m", fromLocationCode: "RCV-9", toBarcode: "Z-99" },
      });
      const { calls } = gateway();
      renderPutaway();

      await screen.findByRole("radio", { name: /SKU-100/ });

      expect(posts(calls)).toHaveLength(0);
      expect(screen.queryByText(/waiting to sync/)).toBeNull();
      expect(screen.queryByText(/SKU-9/)).toBeNull();
      expect(getPutawayQueue()).toHaveLength(1);
    });

    it("a put-away left on the phone by a closed app is sent when the screen opens online", async () => {
      const entry = enqueuePutaway({
        ownerSub: "user-1",
        organizationId: "org-1",
        body: {
          inventory_item_id: "item-1",
          from_location_id: "loc-rcv-1",
          to_barcode: "A-01-03",
          quantity: "3",
          captured_at: "2026-09-28T10:00:00.000Z",
          occurred_on: "2026-09-28",
        },
        summary: { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" },
      });
      const { calls } = gateway();
      renderPutaway();

      await waitFor(() => expect(posts(calls)).toHaveLength(1));
      expect(posts(calls)[0]?.headers["idempotency-key"]).toBe(entry.idempotencyKey);
      expect(posts(calls)[0]?.body).toMatchObject({ occurred_on: "2026-09-28", captured_at: "2026-09-28T10:00:00.000Z" });
      await waitFor(() => expect(getPutawayQueue()).toEqual([]));
    });
  });

  describe("the server says no", () => {
    it("404: nothing is queued, the server's words are shown, the label can be fixed, and the retry is a new request", async () => {
      let attempt = 0;
      const { calls } = gateway({
        [PUTAWAY]: () => {
          attempt += 1;
          return attempt === 1 ? problem(404, "No bin has that label.") : json(makePutawayOut(), 201);
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-0O");
      await confirm(user, "Put away here anyway");

      expect(await screen.findByText("No bin has that label.")).toBeTruthy();
      expect(getPutawayQueue()).toEqual([]);
      expect(screen.queryByText(/waiting to sync/)).toBeNull();
      const field = await screen.findByLabelText("Bin label");
      expect((field as HTMLInputElement).value).toBe("A-01-0O");

      await user.clear(field);
      await user.type(field, "A-01-03");
      await user.click(screen.getByRole("button", { name: "Use this label" }));
      await confirm(user);

      await screen.findByText("Put away recorded");
      expect(posts(calls)).toHaveLength(2);
      expect(posts(calls)[1]?.headers["idempotency-key"]).not.toBe(posts(calls)[0]?.headers["idempotency-key"]);
    });

    it("after a refused label the next scan replaces it — a scanner types at the caret, so the old text is selected", async () => {
      let attempt = 0;
      const { calls } = gateway({
        [PUTAWAY]: () => {
          attempt += 1;
          return attempt === 1 ? problem(404, "No bin has that label.") : json(makePutawayOut(), 201);
        },
      });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "WRONG");
      await confirm(user, "Put away here anyway");
      await screen.findByText("No bin has that label.");

      const field = (await screen.findByLabelText("Bin label")) as HTMLInputElement;
      await waitFor(() => expect(document.activeElement).toBe(field));
      expect(field.value).toBe("WRONG");
      expect([field.selectionStart, field.selectionEnd]).toEqual([0, 5]);

      // A keyboard-wedge scanner types what it reads into whatever has focus.
      await user.keyboard("A-01-03{Enter}");

      // Matches the suggested bin exactly, so no warning: it did not become "WRONGA-01-03".
      await confirm(user);
      await screen.findByText("Put away recorded");
      expect(posts(calls)[1]?.body).toMatchObject({ to_barcode: "A-01-03" });
    });

    it("the same after tapping Change on a label that was accepted by mistake", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "B-02-01");
      await screen.findByText("That isn't the suggested bin");

      await user.click(screen.getByRole("button", { name: "Change bin label" }));
      await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Bin label")));
      await user.keyboard("A-01-03{Enter}");

      expect(await screen.findByRole("button", { name: "Put away" })).toBeTruthy();
      expect(screen.queryByText("That isn't the suggested bin")).toBeNull();
    });

    it.each([
      [409, "That stock is reserved for an order."],
      [422, "Put-away only moves stock out of receiving or staging."],
    ])("%i: the server's sentence is shown, the form stays put, and nothing is queued", async (status, detail) => {
      gateway({ [PUTAWAY]: () => problem(status, detail) });
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      expect(await screen.findByText(detail)).toBeTruthy();
      expect(screen.getByRole("button", { name: "Put away" })).toBeTruthy();
      expect(getPutawayQueue()).toEqual([]);
    });
  });

  describe("leaving the screen while a put-away is in flight", () => {
    /** The provider lives above the screens, as in the app shell, so Home is reachable and the
     *  queue — unlike the Put away screen — survives the worker walking away from it. */
    function Shell() {
      const navigate = useNavigate();
      return (
        <PutawayQueueProvider ownerSub="user-1" organizationId="org-1">
          <button type="button" onClick={() => navigate("/")}>
            Go home
          </button>
          <Routes>
            <Route path="putaway" element={<PutawayPage />} />
            <Route path="*" element={<p>Home screen</p>} />
          </Routes>
        </PutawayQueueProvider>
      );
    }

    function renderShell() {
      return renderScreen(<Shell />, { at: "/putaway", path: "/*", grants: GRANTS });
    }

    it.each([
      [404, "No bin has that label."],
      [409, "That stock is reserved for an order."],
      [422, "Put-away only moves stock out of receiving or staging."],
    ])("a %i that arrives after the worker has left is parked for them to read, not dropped", async (status, detail) => {
      let answer: (() => void) | undefined;
      const { calls } = gateway({
        [PUTAWAY]: () =>
          new Promise<Response>((resolve) => {
            answer = () => resolve(problem(status, detail));
          }),
      });
      const user = userEvent.setup();
      renderShell();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);
      await waitFor(() => expect(posts(calls)).toHaveLength(1));

      await user.click(screen.getByRole("button", { name: "Go home" }));
      expect(await screen.findByText("Home screen")).toBeTruthy();
      answer?.();

      await waitFor(() => expect(getPutawayQueue()).toHaveLength(1));
      expect(getPutawayQueue()[0]).toMatchObject({
        status: "needs_attention",
        attention: { httpStatus: status, message: detail },
      });
      expect(getPutawayQueue()[0]?.body).not.toHaveProperty("lat_micro");
    });

    it("a refusal that arrives while the screen is still there is shown on it and leaves nothing behind", async () => {
      let answer: (() => void) | undefined;
      const { calls } = gateway({
        [PUTAWAY]: () =>
          new Promise<Response>((resolve) => {
            answer = () => resolve(problem(409, "That stock is reserved for an order."));
          }),
      });
      const user = userEvent.setup();
      renderShell();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);
      await waitFor(() => expect(posts(calls)).toHaveLength(1));

      answer?.();

      expect(await screen.findByText("That stock is reserved for an order.")).toBeTruthy();
      expect(getPutawayQueue()).toEqual([]);
    });
  });

  describe("parked put-aways", () => {
    function parkOne() {
      const entry = enqueuePutaway({
        ownerSub: "user-1",
        organizationId: "org-1",
        body: {
          inventory_item_id: "item-1",
          from_location_id: "loc-rcv-1",
          to_barcode: "A-01-03",
          quantity: "4",
          lat_micro: 12_971_599,
          lng_micro: 77_594_563,
        },
        summary: { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" },
      });
      parkPutaway(entry.idempotencyKey, { httpStatus: 409, message: "That stock is reserved for an order." });
      return entry;
    }

    it("shows what the server refused, in its words, and offers to discard it", async () => {
      parkOne();
      const { calls } = gateway();
      renderPutaway();

      expect(await screen.findByText("Needs attention")).toBeTruthy();
      expect(screen.getByText("Not recorded: 4 m of SKU-100 to A-01-03")).toBeTruthy();
      expect(screen.getByText("That stock is reserved for an order.")).toBeTruthy();
      expect(posts(calls)).toHaveLength(0);
    });

    it("discarding asks first, then removes it from the phone", async () => {
      parkOne();
      gateway();
      const user = userEvent.setup();
      renderPutaway();

      await user.click(await screen.findByRole("button", { name: "Discard" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Discard this put-away?")).toBeTruthy();
      expect(getPutawayQueue()).toHaveLength(1);

      await user.click(within(dialog).getByRole("button", { name: "Discard" }));

      await waitFor(() => expect(getPutawayQueue()).toEqual([]));
      expect(screen.queryByText("Needs attention")).toBeNull();
    });

    it("cancelling the discard keeps it", async () => {
      parkOne();
      gateway();
      const user = userEvent.setup();
      renderPutaway();

      await user.click(await screen.findByRole("button", { name: "Discard" }));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));

      expect(getPutawayQueue()).toHaveLength(1);
    });

    it("a parked put-away does not stop the next one from going through", async () => {
      parkOne();
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");
      await confirm(user);

      await screen.findByText("Put away recorded");
      expect(posts(calls)).toHaveLength(1);
      expect(screen.getByText("Needs attention")).toBeTruthy();
    });
  });

  describe("choosing the stock", () => {
    it("filters what has loaded by SKU, item, lot, roll or bin", async () => {
      gateway({
        [STOCK]: () =>
          json({
            items: [
              makeStockRow({ id: "a", sku: "SKU-100", item_name: "Cotton roll" }),
              makeStockRow({ id: "b", sku: "SKU-200", item_name: "Denim", roll_no: "R-55", location_code: "STG-02" }),
            ],
            next_cursor: null,
          }),
      });
      const user = userEvent.setup();
      renderPutaway();

      await screen.findByRole("radio", { name: /SKU-100/ });
      await user.type(screen.getByLabelText("Find stock"), "r-55");

      expect(screen.queryByRole("radio", { name: /SKU-100/ })).toBeNull();
      expect(screen.getByRole("radio", { name: /SKU-200/ })).toBeTruthy();
    });

    it("says so when nothing matches", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await screen.findByRole("radio", { name: /SKU-100/ });
      await user.type(screen.getByLabelText("Find stock"), "zzz");

      expect(screen.getByText(/Nothing matches that/)).toBeTruthy();
    });

    it("leaves out a bucket with nothing available", async () => {
      gateway({
        [STOCK]: () =>
          json({
            items: [
              makeStockRow({ id: "a", sku: "SKU-100" }),
              makeStockRow({ id: "b", sku: "SKU-RESERVED", available_qty: "0", reserved_qty: "20" }),
            ],
            next_cursor: null,
          }),
      });
      renderPutaway();

      await screen.findByRole("radio", { name: /SKU-100/ });
      expect(screen.queryByRole("radio", { name: /SKU-RESERVED/ })).toBeNull();
    });

    it("pages on with the server's cursor, appending, and only while there is more", async () => {
      const { calls } = gateway({
        [STOCK]: (call) =>
          call.search.get("cursor") === "next-page"
            ? json({ items: [makeStockRow({ id: "b", sku: "SKU-200", item_name: "Denim" })], next_cursor: null })
            : json({ items: [makeStockRow({ id: "a" })], next_cursor: "next-page" }),
      });
      const user = userEvent.setup();
      renderPutaway();

      await screen.findByRole("radio", { name: /SKU-100/ });
      await user.click(screen.getByRole("button", { name: "Load more stock" }));

      expect(await screen.findByRole("radio", { name: /SKU-200/ })).toBeTruthy();
      expect(screen.getByRole("radio", { name: /SKU-100/ })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Load more stock" })).toBeNull();
      expect(calls.filter((call) => call.key === STOCK).map((call) => call.search.get("cursor"))).toEqual([null, "next-page"]);
    });

    it("an empty result is an empty state that says what to do next", async () => {
      gateway({ [STOCK]: () => json({ items: [], next_cursor: null }) });
      renderPutaway();

      expect(await screen.findByText("No stock to put away")).toBeTruthy();
    });

    it("a failed load is an error state with a retry that works", async () => {
      let attempt = 0;
      gateway({
        [STOCK]: () => {
          attempt += 1;
          return attempt === 1 ? problem(500, "Stock is unavailable right now.") : json({ items: [makeStockRow()], next_cursor: null });
        },
      });
      const user = userEvent.setup();
      renderPutaway();

      expect(await screen.findByText("Couldn't load stock")).toBeTruthy();
      expect(screen.getByText("Stock is unavailable right now.")).toBeTruthy();
      await user.click(screen.getByRole("button", { name: "Try again" }));

      expect(await screen.findByRole("radio", { name: /SKU-100/ })).toBeTruthy();
    });

    it("Change goes back to the list", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);

      await user.click(await screen.findByRole("button", { name: "Change stock" }));

      expect(await screen.findByRole("radio", { name: /SKU-100/ })).toBeTruthy();
    });
  });

  describe("focus follows the flow — each step replaces the control that had it", () => {
    it("the bin field after choosing stock, the accepted label after it, Put away more after the result", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();

      // The next scan from a keyboard-wedge scanner has to land in the bin field, not the quantity.
      await pickStock(user);
      await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Bin label")));

      await user.type(screen.getByLabelText("Bin label"), "A-01-03{Enter}");
      // Not on the confirm button: a scanner's trailing Enter, or a second read, must not submit.
      await waitFor(() => expect(screen.getByRole("button", { name: "Put away" })).toBeTruthy());
      expect(document.activeElement?.textContent).toContain("A-01-03");
      expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Put away" }));

      await confirm(user);
      await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Put away more" })));
    });

    it("a stray Enter after the label is accepted does not confirm the put-away", async () => {
      const { calls } = gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);

      await user.keyboard("A-01-03{Enter}{Enter}");

      expect(await screen.findByRole("button", { name: "Put away" })).toBeTruthy();
      expect(posts(calls)).toHaveLength(0);
    });

    it("back to the field when the bin label or the stock is changed", async () => {
      gateway();
      const user = userEvent.setup();
      renderPutaway();
      await pickStock(user);
      await enterBin(user, "A-01-03");

      await user.click(screen.getByRole("button", { name: "Change bin label" }));
      await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Bin label")));

      await user.click(screen.getByRole("button", { name: "Change stock" }));
      await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Find stock")));
    });

    it("does not grab focus when the screen first opens — that would raise a phone's keyboard unasked", async () => {
      gateway();
      renderPutaway();

      await screen.findByRole("radio", { name: /SKU-100/ });

      expect(document.activeElement).toBe(document.body);
    });
  });

  describe("access", () => {
    it("a worker without the transfer permission gets the denial, and nothing is fetched", async () => {
      const { calls } = gateway();
      renderPutaway(["operations.inventory.stock.read:ORG"]);

      expect(await screen.findByText("You can't open put away right now")).toBeTruthy();
      expect(calls).toHaveLength(0);
    });

    it("the permission must be ORG — an OWN grant is not enough", async () => {
      const { calls } = gateway();
      renderPutaway(["operations.inventory.stock.transfer:OWN"]);

      expect(await screen.findByText("You can't open put away right now")).toBeTruthy();
      expect(calls).toHaveLength(0);
    });
  });

  describe("at phone width", () => {
    it("is one column of full-width controls: no table, no fixed widths to overflow a 360px screen", async () => {
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }));
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 360 });
      gateway();
      const user = userEvent.setup();
      const { container } = renderPutaway();

      await pickStock(user);
      await enterBin(user, "B-02-01");
      await screen.findByText("That isn't the suggested bin");

      expect(container.querySelector("table")).toBeNull();
      expect(container.querySelectorAll("[style*='width']")).toHaveLength(0);
      // Every action a worker needs at the end of the flow is a real, reachable button.
      expect(screen.getByRole("button", { name: "Put away here anyway" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Change stock" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Change bin label" })).toBeTruthy();
    });
  });
});
