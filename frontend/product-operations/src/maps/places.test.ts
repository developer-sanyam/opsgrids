import { describe, expect, it, vi } from "vitest";

import { createPlacesAdapter } from "./places";

interface FakePrediction {
  placeId: string;
  text: { text: string };
  mainText?: { text: string };
  secondaryText?: { text: string };
  toPlace: () => FakePlace;
}
interface FakePlace {
  location: { lat: () => number; lng: () => number } | null;
  formattedAddress: string | null;
  fetchFields: ReturnType<typeof vi.fn>;
}

function fakePlace(lat: number, lng: number, address: string | null): FakePlace {
  return {
    location: { lat: () => lat, lng: () => lng },
    formattedAddress: address,
    fetchFields: vi.fn().mockResolvedValue(undefined),
  };
}

function library(predictions: FakePrediction[]) {
  const tokens: object[] = [];
  const fetchAutocompleteSuggestions = vi.fn().mockResolvedValue({
    suggestions: [...predictions.map((placePrediction) => ({ placePrediction })), { placePrediction: null }],
  });
  class AutocompleteSessionToken {
    constructor() {
      tokens.push(this);
    }
  }
  const lib = {
    AutocompleteSessionToken,
    AutocompleteSuggestion: { fetchAutocompleteSuggestions },
  } as unknown as google.maps.PlacesLibrary;
  return { lib, tokens, fetchAutocompleteSuggestions };
}

const prediction = (place: FakePlace, id = "p1"): FakePrediction => ({
  placeId: id,
  text: { text: "Plot 5, MIDC, Bhiwandi, Maharashtra" },
  mainText: { text: "Plot 5, MIDC" },
  secondaryText: { text: "Bhiwandi, Maharashtra" },
  toPlace: () => place,
});

describe("createPlacesAdapter", () => {
  it("maps suggestions to Combobox options and skips the ones that are not place predictions", async () => {
    const { lib, fetchAutocompleteSuggestions } = library([prediction(fakePlace(19.3, 73.06, "x"))]);
    const adapter = createPlacesAdapter(lib);
    const options = await adapter.search("plot 5", new AbortController().signal);
    expect(options).toEqual([{ id: "p1", label: "Plot 5, MIDC", sublabel: "Bhiwandi, Maharashtra" }]);
    expect(fetchAutocompleteSuggestions).toHaveBeenCalledWith(expect.objectContaining({ input: "plot 5" }));
  });

  it("uses one session token for a typing session and a fresh one after a pick", async () => {
    const place = fakePlace(19.3, 73.06, "Plot 5, MIDC, Bhiwandi");
    const { lib, tokens, fetchAutocompleteSuggestions } = library([prediction(place)]);
    const adapter = createPlacesAdapter(lib);
    await adapter.search("plo", new AbortController().signal);
    await adapter.search("plot", new AbortController().signal);
    const used = fetchAutocompleteSuggestions.mock.calls.map((call) => (call[0] as { sessionToken: object }).sessionToken);
    expect(used[0]).toBe(used[1]);
    expect(tokens).toHaveLength(1);

    await adapter.resolve("p1");
    expect(tokens).toHaveLength(2);
    await adapter.search("next", new AbortController().signal);
    const next = fetchAutocompleteSuggestions.mock.calls[2]?.[0] as { sessionToken: object };
    expect(next.sessionToken).not.toBe(used[0]);
  });

  it("resolves a pick to the address and an integer micro-degree position", async () => {
    const place = fakePlace(19.3012345, 73.0612349, "Plot 5, MIDC, Bhiwandi, Maharashtra");
    const { lib } = library([prediction(place)]);
    const adapter = createPlacesAdapter(lib);
    await adapter.search("plot", new AbortController().signal);
    const selection = await adapter.resolve("p1");
    expect(place.fetchFields).toHaveBeenCalledWith({ fields: ["formattedAddress", "location"] });
    expect(selection).toEqual({
      address: "Plot 5, MIDC, Bhiwandi, Maharashtra",
      lat_micro: 19_301_235,
      lng_micro: 73_061_235,
    });
  });

  it("returns null for a suggestion it does not know, a place with no position, or an impossible one", async () => {
    const noLocation = fakePlace(0, 0, "x");
    noLocation.location = null;
    const impossible = fakePlace(123, 0, "y");
    const { lib } = library([
      prediction(noLocation, "none"),
      prediction(impossible, "bad"),
    ]);
    const adapter = createPlacesAdapter(lib);
    expect(await adapter.resolve("never-offered")).toBeNull();
    await adapter.search("zz", new AbortController().signal);
    expect(await adapter.resolve("none")).toBeNull();
    await adapter.search("zz", new AbortController().signal);
    expect(await adapter.resolve("bad")).toBeNull();
  });

  it("drops the results of a search the control already abandoned", async () => {
    const { lib } = library([prediction(fakePlace(19, 73, "x"))]);
    const adapter = createPlacesAdapter(lib);
    const controller = new AbortController();
    const pending = adapter.search("plot", controller.signal);
    controller.abort();
    expect(await pending).toEqual([]);
  });

  it("lets a refused request reach the control, which shows its own error", async () => {
    const { lib, fetchAutocompleteSuggestions } = library([]);
    fetchAutocompleteSuggestions.mockRejectedValueOnce(new Error("REQUEST_DENIED"));
    const adapter = createPlacesAdapter(lib);
    await expect(adapter.search("plot", new AbortController().signal)).rejects.toThrow("REQUEST_DENIED");
  });
});
