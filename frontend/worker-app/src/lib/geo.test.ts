import { afterEach, describe, expect, it, vi } from "vitest";

import { removeGeolocation, stubGeolocation } from "@/worker/testKit";
import type { GeoFailure, GeoSuccess } from "@/worker/testKit";

import { GEO_TIMEOUT_MS, degreesToMicro, geoSkipNotice, requestGeoFix, toGeoFix } from "./geo";
import type { GeoSkipReason } from "./geo";

type Success = GeoSuccess;
type Failure = GeoFailure;

/** `test-setup.ts` fakes only `Date`; the guard timer needs `setTimeout` faked as well. */
function useFakeTimeouts() {
  vi.useRealTimers();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
}

afterEach(removeGeolocation);

describe("degreesToMicro", () => {
  it("rounds to integer micro-degrees", () => {
    expect(degreesToMicro(12.9715987)).toBe(12971599);
    expect(degreesToMicro(-77.5945627)).toBe(-77594563);
    expect(degreesToMicro(0)).toBe(0);
  });

  it("never returns a negative zero, which would serialise oddly", () => {
    expect(Object.is(degreesToMicro(-0.0000001), 0)).toBe(true);
  });

  it("returns null for anything that is not a finite number", () => {
    expect(degreesToMicro(Number.NaN)).toBeNull();
    expect(degreesToMicro(Number.POSITIVE_INFINITY)).toBeNull();
    expect(degreesToMicro(Number.NEGATIVE_INFINITY)).toBeNull();
  });
});

describe("toGeoFix", () => {
  it("converts a position to integers, accuracy included", () => {
    expect(toGeoFix({ latitude: 12.9715987, longitude: 77.5945627, accuracy: 12.4 })).toEqual({
      lat_micro: 12971599,
      lng_micro: 77594563,
      accuracy_m: 12,
    });
  });

  it("omits a coordinate pair that has a non-finite half — both travel together or not at all", () => {
    expect(toGeoFix({ latitude: Number.NaN, longitude: 77.5, accuracy: 5 })).toBeNull();
    expect(toGeoFix({ latitude: 12.9, longitude: Number.POSITIVE_INFINITY, accuracy: 5 })).toBeNull();
  });

  it("drops a pair outside the globe rather than leave the server to refuse it", () => {
    expect(toGeoFix({ latitude: 91, longitude: 10, accuracy: 5 })).toBeNull();
    expect(toGeoFix({ latitude: 10, longitude: -181, accuracy: 5 })).toBeNull();
    expect(toGeoFix({ latitude: 90, longitude: 180, accuracy: 5 })).toEqual({
      lat_micro: 90_000_000,
      lng_micro: 180_000_000,
      accuracy_m: 5,
    });
  });

  it("a bad accuracy costs only the accuracy", () => {
    expect(toGeoFix({ latitude: 1, longitude: 2, accuracy: Number.NaN })).toEqual({ lat_micro: 1_000_000, lng_micro: 2_000_000 });
    expect(toGeoFix({ latitude: 1, longitude: 2, accuracy: -3 })).toEqual({ lat_micro: 1_000_000, lng_micro: 2_000_000 });
  });
});

describe("requestGeoFix", () => {
  it("asks once, for a high-accuracy fix with a five second timeout", async () => {
    const spy = stubGeolocation((ok) => ok({ coords: { latitude: 12.97, longitude: 77.59, accuracy: 8 } }));

    const result = await requestGeoFix({ online: true });

    expect(result).toEqual({ kind: "fix", fix: { lat_micro: 12_970_000, lng_micro: 77_590_000, accuracy_m: 8 } });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0]?.[2]).toEqual({ enableHighAccuracy: true, timeout: GEO_TIMEOUT_MS, maximumAge: 0 });
    expect(GEO_TIMEOUT_MS).toBe(5000);
  });

  const failures: [string, (ok: Success, fail: Failure) => void, GeoSkipReason][] = [
    ["permission denied", (_ok, fail) => fail({ code: 1 }), "denied"],
    ["position unavailable", (_ok, fail) => fail({ code: 2 }), "unavailable"],
    ["the browser's own timeout", (_ok, fail) => fail({ code: 3 }), "timeout"],
    ["an unusable reading", (ok) => ok({ coords: { latitude: Number.NaN, longitude: 1, accuracy: 1 } }), "invalid"],
  ];
  it.each(failures)("%s is a skip with a reason, never a throw", async (_name, behave, reason) => {
    stubGeolocation(behave);
    expect(await requestGeoFix({ online: true })).toEqual({ kind: "skipped", reason });
  });

  it("a browser with no geolocation at all is unsupported", async () => {
    expect(await requestGeoFix({ online: true })).toEqual({ kind: "skipped", reason: "unsupported" });
  });

  it("a WebView that throws instead of calling back is unsupported too", async () => {
    stubGeolocation(() => {
      throw new Error("locked down");
    });
    expect(await requestGeoFix({ online: true })).toEqual({ kind: "skipped", reason: "unsupported" });
  });

  it("offline never asks the phone for a position", async () => {
    const spy = stubGeolocation((ok) => ok({ coords: { latitude: 1, longitude: 2, accuracy: 3 } }));
    expect(await requestGeoFix({ online: false })).toEqual({ kind: "skipped", reason: "offline" });
    expect(spy).not.toHaveBeenCalled();
  });

  it("an ignored permission prompt cannot hold the put-away up: a guard timer gives up after ~6 s", async () => {
    useFakeTimeouts();
    stubGeolocation(() => undefined); // never calls back, as when the prompt is left unanswered

    const pending = requestGeoFix({ online: true });
    await vi.advanceTimersByTimeAsync(GEO_TIMEOUT_MS + 1000);

    expect(await pending).toEqual({ kind: "skipped", reason: "timeout" });
  });

  it("a fix that arrives after the guard fired is ignored — the first answer wins", async () => {
    useFakeTimeouts();
    let late: Success | undefined;
    stubGeolocation((ok) => {
      late = ok;
    });

    const pending = requestGeoFix({ online: true });
    await vi.advanceTimersByTimeAsync(GEO_TIMEOUT_MS + 1000);
    late?.({ coords: { latitude: 1, longitude: 2, accuracy: 3 } });

    expect(await pending).toEqual({ kind: "skipped", reason: "timeout" });
  });
});

describe("geoSkipNotice", () => {
  it("says something specific, and calm, for every reason", () => {
    const reasons: GeoSkipReason[] = ["unsupported", "denied", "timeout", "unavailable", "offline", "invalid"];
    const notices = reasons.map(geoSkipNotice);
    expect(new Set(notices).size).toBe(reasons.length);
    for (const notice of notices) expect(notice).toMatch(/location/i);
  });
});
