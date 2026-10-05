/**
 * The three Maps components, against a stand-in for `@vis.gl/react-google-maps` (the real one needs
 * Google's script and a browser). What is pinned here is what this app decides: which sites get a
 * pin, what the popup says, that a loader error or an auth refusal tells the page once and with no
 * detail, that the key goes to the provider and nowhere else, and that the places lookup fills the
 * form from a chosen suggestion.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import type { WarehouseMapSiteOut } from "@/api/types";
import LocationPinMap from "./LocationPinMap";
import { SLOW_LOAD_MS } from "@/lib/useSlowLoad";
import { MapShell } from "./MapShell";
import { SCRIPT_HANG_MS } from "./scriptStatus";
import PlacesLookup from "./PlacesLookup";
import SitesMap from "./SitesMap";

const state = {
  status: "LOADED" as string,
  places: null as unknown,
  providerKey: "",
  providerOnError: undefined as ((e: unknown) => void) | undefined,
};

vi.mock("@vis.gl/react-google-maps", () => ({
  APILoadingStatus: { NOT_LOADED: "NOT_LOADED", LOADING: "LOADING", LOADED: "LOADED", FAILED: "FAILED", AUTH_FAILURE: "AUTH_FAILURE" },
  APIProvider: ({ apiKey, onError, children }: { apiKey: string; onError?: (e: unknown) => void; children: ReactNode }) => {
    state.providerKey = apiKey;
    state.providerOnError = onError;
    return <div data-testid="api-provider">{children}</div>;
  },
  useApiLoadingStatus: () => state.status,
  useMapsLibrary: () => state.places,
  Map: (props: Record<string, unknown> & { children?: ReactNode }) => (
    <div data-testid="map" data-center={JSON.stringify(props.defaultCenter ?? null)} data-zoom={String(props.defaultZoom ?? "")} data-bounds={JSON.stringify(props.defaultBounds ?? null)}>
      {props.children}
    </div>
  ),
  Marker: (props: { title?: string; onClick?: () => void; position: { lat: number; lng: number } }) => (
    <button type="button" data-testid="marker" data-lat={props.position.lat} data-lng={props.position.lng} onClick={props.onClick}>
      marker {props.title}
    </button>
  ),
  InfoWindow: (props: { headerContent?: ReactNode; children?: ReactNode; onCloseClick?: () => void }) => (
    <div role="dialog" aria-label="popup">
      {props.headerContent}
      {props.children}
      <button type="button" onClick={props.onCloseClick}>
        close popup
      </button>
    </div>
  ),
  Circle: (props: { radius?: number }) => <div data-testid="accuracy-circle" data-radius={props.radius} />,
}));

const SITE: WarehouseMapSiteOut = {
  id: "wh-1",
  code: "BHW",
  name: "Bhiwandi DC",
  location: "Plot 5, MIDC",
  lat_micro: 19_300_000,
  lng_micro: 73_060_000,
  floor_count: 3,
  stocked_location_count: 120,
};
const SITE_2: WarehouseMapSiteOut = { ...SITE, id: "wh-2", name: "Pune Hub", lat_micro: 18_520_000, lng_micro: 73_856_000, floor_count: 1, stocked_location_count: 1 };

beforeEach(() => {
  state.status = "LOADED";
  state.places = null;
  state.providerKey = "";
  state.providerOnError = undefined;
});

afterEach(() => {
  vi.useRealTimers();
});

/** Moves the clock, inside `act`, so the timers a component armed fire and React sees their updates. */
function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe("MapShell", () => {
  it("hears Google refusing the key through gm_authFailure — which no provider status reports — and tells the page once", () => {
    const onFailure = vi.fn();
    render(
      <MapShell apiKey="k" onFailure={onFailure}>
        <p>inside</p>
      </MapShell>,
    );
    expect(typeof window.gm_authFailure).toBe("function");
    expect(onFailure).not.toHaveBeenCalled();

    window.gm_authFailure?.();
    window.gm_authFailure?.();
    expect(onFailure).toHaveBeenCalledTimes(1);
    expect(onFailure).toHaveBeenCalledWith("auth_failure");
  });

  it("keeps whatever handler was there, and puts it back when it goes", () => {
    const previous = vi.fn();
    window.gm_authFailure = previous;
    const onFailure = vi.fn();
    const { unmount } = render(
      <MapShell apiKey="k" onFailure={onFailure}>
        <p>inside</p>
      </MapShell>,
    );
    window.gm_authFailure?.();
    expect(previous).toHaveBeenCalledTimes(1);
    expect(onFailure).toHaveBeenCalledWith("auth_failure");
    unmount();
    expect(window.gm_authFailure).toBe(previous);
    delete window.gm_authFailure;
  });

  it("leaves no global behind when it unmounts", () => {
    delete window.gm_authFailure;
    const { unmount } = render(
      <MapShell apiKey="k" onFailure={vi.fn()}>
        <p>inside</p>
      </MapShell>,
    );
    unmount();
    expect(window.gm_authFailure).toBeUndefined();
  });

  it("tells the page only once even when the script error and the refusal both happen", () => {
    const onFailure = vi.fn();
    render(
      <MapShell apiKey="k" onFailure={onFailure}>
        <p>inside</p>
      </MapShell>,
    );
    state.providerOnError?.(new Error("script"));
    window.gm_authFailure?.();
    expect(onFailure).toHaveBeenCalledTimes(1);
    expect(onFailure).toHaveBeenCalledWith("script_error");
  });

  describe("a script that never answers", () => {
    function renderShell(onFailure = vi.fn()) {
      const view = render(
        <MapShell apiKey="k" onFailure={onFailure}>
          <p>inside</p>
        </MapShell>,
      );
      return { onFailure, ...view };
    }

    it("gives up on the map, with the fixed script reason, once the script has been pending for the cap", () => {
      vi.useFakeTimers();
      state.status = "LOADING";
      const { onFailure } = renderShell();
      advance(SCRIPT_HANG_MS - 1);
      expect(onFailure).not.toHaveBeenCalled();
      advance(1);
      expect(onFailure).toHaveBeenCalledTimes(1);
      expect(onFailure).toHaveBeenCalledWith("script_error");
      advance(SCRIPT_HANG_MS);
      expect(onFailure).toHaveBeenCalledTimes(1);
    });

    it("counts a script that has not started yet as pending", () => {
      vi.useFakeTimers();
      state.status = "NOT_LOADED";
      const { onFailure } = renderShell();
      advance(SCRIPT_HANG_MS);
      expect(onFailure).toHaveBeenCalledWith("script_error");
    });

    it("keeps the clock running when the page re-renders, rather than restarting it each time", () => {
      vi.useFakeTimers();
      state.status = "LOADING";
      const first = vi.fn();
      const { rerender } = renderShell(first);
      advance(SCRIPT_HANG_MS - 5_000);
      const second = vi.fn();
      rerender(
        <MapShell apiKey="k" onFailure={second}>
          <p>inside</p>
        </MapShell>,
      );
      advance(5_000);
      expect(first).not.toHaveBeenCalled();
      expect(second).toHaveBeenCalledWith("script_error");
    });

    it("lets a script that arrives in time be", () => {
      vi.useFakeTimers();
      state.status = "LOADING";
      const { onFailure, rerender } = renderShell();
      advance(SCRIPT_HANG_MS - 1_000);
      state.status = "LOADED";
      rerender(
        <MapShell apiKey="k" onFailure={onFailure}>
          <p>inside</p>
        </MapShell>,
      );
      advance(SCRIPT_HANG_MS);
      expect(onFailure).not.toHaveBeenCalled();
    });

    it("stops waiting when the map goes away", () => {
      vi.useFakeTimers();
      state.status = "LOADING";
      const { onFailure, unmount } = renderShell();
      unmount();
      advance(SCRIPT_HANG_MS);
      expect(onFailure).not.toHaveBeenCalled();
    });
  });
});

describe("SitesMap", () => {
  function renderSites(overrides: Partial<Pick<Parameters<typeof SitesMap>[0], "sites" | "selectedId">> = {}) {
    const onSelect = vi.fn();
    const onOpenFloors = vi.fn();
    const onFailure = vi.fn();
    render(
      <SitesMap
        apiKey="test-key"
        sites={[SITE, SITE_2]}
        selectedId={null}
        {...overrides}
        onSelect={onSelect}
        onOpenFloors={onOpenFloors}
        onFailure={onFailure}
      />,
    );
    return { onSelect, onOpenFloors, onFailure };
  }

  it("gives the key to the provider and pins every site in degrees", () => {
    renderSites();
    expect(state.providerKey).toBe("test-key");
    const markers = screen.getAllByTestId("marker");
    expect(markers).toHaveLength(2);
    expect(markers[0]?.getAttribute("data-lat")).toBe("19.3");
    expect(markers[0]?.getAttribute("data-lng")).toBe("73.06");
  });

  it("frames several sites by their bounds and a single site by its centre", () => {
    renderSites();
    const bounds = JSON.parse(screen.getByTestId("map").getAttribute("data-bounds") ?? "null");
    expect(bounds).toMatchObject({ north: 19.3, south: 18.52, east: 73.856, west: 73.06 });
  });

  it("centres a lone site at a neighbourhood zoom", () => {
    renderSites({ sites: [SITE] });
    expect(JSON.parse(screen.getByTestId("map").getAttribute("data-center") ?? "null")).toEqual({ lat: 19.3, lng: 73.06 });
    expect(screen.getByTestId("map").getAttribute("data-zoom")).toBe("13");
  });

  it("selects a site when its pin is clicked", async () => {
    const user = userEvent.setup();
    const props = renderSites();
    await user.click(screen.getByRole("button", { name: "marker Pune Hub" }));
    expect(props.onSelect).toHaveBeenCalledWith("wh-2");
  });

  it("opens a popup for the selected site: name, address, floors, stocked bins and the way into its floors", async () => {
    const user = userEvent.setup();
    const props = renderSites({ selectedId: "wh-1" });
    const popup = screen.getByRole("dialog", { name: "popup" });
    expect(popup.textContent).toContain("Bhiwandi DC");
    expect(popup.textContent).toContain("Plot 5, MIDC");
    expect(popup.textContent).toContain("3 floors · 120 stocked bins");
    await user.click(screen.getByRole("button", { name: "Open floors" }));
    expect(props.onOpenFloors).toHaveBeenCalledWith("wh-1");
    await user.click(screen.getByRole("button", { name: "close popup" }));
    expect(props.onSelect).toHaveBeenCalledWith(null);
  });

  it("uses singular words for one floor and one bin, and says when there is no address", () => {
    renderSites({ sites: [{ ...SITE_2, location: "" }], selectedId: "wh-2" });
    const popup = screen.getByRole("dialog", { name: "popup" });
    expect(popup.textContent).toContain("1 floor · 1 stocked bin");
    expect(popup.textContent).toContain("No address recorded");
  });

  it("opens no popup when nothing is selected", () => {
    renderSites();
    expect(screen.queryByRole("dialog", { name: "popup" })).toBeNull();
  });

  it("tells the page once, with a fixed reason, when Google refuses the key", () => {
    state.status = "AUTH_FAILURE";
    const props = renderSites();
    expect(props.onFailure).toHaveBeenCalledTimes(1);
    expect(props.onFailure).toHaveBeenCalledWith("auth_failure");
  });

  it("tells the page when the script fails to load, without handing over the loader's error", () => {
    state.status = "FAILED";
    const props = renderSites();
    expect(props.onFailure).toHaveBeenCalledWith("script_error");
    state.status = "LOADED";
    state.providerOnError?.(new Error("Failed to load https://maps.googleapis.com/maps/api/js?key=test-key"));
    expect(props.onFailure).toHaveBeenLastCalledWith("script_error");
    for (const call of props.onFailure.mock.calls) expect(call).toHaveLength(1);
    expect(JSON.stringify(props.onFailure.mock.calls)).not.toContain("test-key");
  });

  it("stays quiet while the script is still loading", () => {
    state.status = "LOADING";
    const props = renderSites();
    expect(props.onFailure).not.toHaveBeenCalled();
  });

  it("puts every word of the popup on a surface of its own, because Google's bubble is white in either theme", () => {
    renderSites({ selectedId: "wh-1" });
    const popup = screen.getByRole("dialog", { name: "popup" });
    // `ink` and `muted` are contrast-checked against `surface` in light and dark (tokens.test.ts); on
    // Google's white bubble the dark theme's pale text is about 1.1:1. So each piece of themed text
    // must sit on a `bg-surface` of its own, not on whatever is behind the popup.
    const themed = Array.from(popup.querySelectorAll<HTMLElement>(".text-ink, .text-muted"));
    expect(themed.length).toBeGreaterThanOrEqual(3);
    for (const node of themed) expect(node.closest(".bg-surface"), node.textContent ?? "").not.toBeNull();
    expect(within(popup).getByText("Bhiwandi DC").closest(".bg-surface")).not.toBeNull();
    expect(within(popup).getByText("Plot 5, MIDC").closest(".bg-surface")).not.toBeNull();
  });

  describe("while Google's script loads", () => {
    it("fills the box with a loading state instead of leaving it blank", () => {
      state.status = "LOADING";
      renderSites();
      const box = screen.getByTestId("sites-map");
      expect(within(box).getByTestId("map-loading").textContent).toContain("Loading the map…");
      expect(box.className).toContain("h-96");
      expect(box.className).toContain("relative");
    });

    it("shows it from the very start, before the provider has begun", () => {
      state.status = "NOT_LOADED";
      renderSites();
      expect(screen.getByTestId("map-loading")).toBeDefined();
    });

    it("says it is taking longer than usual after the slow-load threshold, and not before", () => {
      vi.useFakeTimers();
      state.status = "LOADING";
      renderSites();
      advance(SLOW_LOAD_MS - 1);
      expect(screen.getByTestId("map-loading").textContent).not.toContain("taking longer");
      advance(1);
      expect(screen.getByTestId("map-loading").textContent).toContain("Loading the map is taking longer than usual");
    });

    it("hands over to the list when the script never answers", () => {
      vi.useFakeTimers();
      state.status = "LOADING";
      const props = renderSites();
      advance(SCRIPT_HANG_MS);
      expect(props.onFailure).toHaveBeenCalledTimes(1);
      expect(props.onFailure).toHaveBeenCalledWith("script_error");
    });

    it("goes away once the script has loaded", () => {
      state.status = "LOADED";
      renderSites();
      expect(screen.queryByTestId("map-loading")).toBeNull();
    });
  });
});

describe("LocationPinMap", () => {
  it("pins the position and draws the fix's accuracy as a circle", () => {
    render(<LocationPinMap apiKey="test-key" lat={19.3012} lng={73.0612} accuracyM={25} onFailure={vi.fn()} />);
    const marker = screen.getByTestId("marker");
    expect(marker.getAttribute("data-lat")).toBe("19.3012");
    expect(marker.getAttribute("data-lng")).toBe("73.0612");
    expect(screen.getByTestId("accuracy-circle").getAttribute("data-radius")).toBe("25");
    expect(screen.getByTestId("map").getAttribute("data-zoom")).toBe("18");
  });

  it("zooms out for a coarse fix and draws no circle when there is no accuracy", () => {
    const { unmount } = render(<LocationPinMap apiKey="k" lat={1} lng={2} accuracyM={900} onFailure={vi.fn()} />);
    expect(screen.getByTestId("map").getAttribute("data-zoom")).toBe("14");
    unmount();
    render(<LocationPinMap apiKey="k" lat={1} lng={2} accuracyM={null} onFailure={vi.fn()} />);
    expect(screen.queryByTestId("accuracy-circle")).toBeNull();
  });

  it("reports a refused key", () => {
    state.status = "AUTH_FAILURE";
    const onFailure = vi.fn();
    render(<LocationPinMap apiKey="k" lat={1} lng={2} accuracyM={5} onFailure={onFailure} />);
    expect(onFailure).toHaveBeenCalledWith("auth_failure");
  });

  it("fills its box with a loading state, then the slow note, then gives up, while the script is pending", () => {
    vi.useFakeTimers();
    state.status = "LOADING";
    const onFailure = vi.fn();
    render(<LocationPinMap apiKey="k" lat={1} lng={2} accuracyM={5} onFailure={onFailure} />);
    const box = screen.getByTestId("location-pin-map");
    expect(within(box).getByTestId("map-loading").textContent).toContain("Loading the map…");
    advance(SLOW_LOAD_MS);
    expect(within(box).getByTestId("map-loading").textContent).toContain("taking longer than usual");
    expect(onFailure).not.toHaveBeenCalled();
    advance(SCRIPT_HANG_MS - SLOW_LOAD_MS);
    expect(onFailure).toHaveBeenCalledWith("script_error");
  });

  it("shows no loading state once the script has loaded", () => {
    render(<LocationPinMap apiKey="k" lat={1} lng={2} accuracyM={5} onFailure={vi.fn()} />);
    expect(screen.queryByTestId("map-loading")).toBeNull();
  });
});

describe("PlacesLookup", () => {
  function fakePlaces() {
    const place = {
      location: { lat: () => 19.3012345, lng: () => 73.0612349 },
      formattedAddress: "Plot 5, MIDC, Bhiwandi, Maharashtra",
      fetchFields: vi.fn().mockResolvedValue(undefined),
    };
    const prediction = {
      placeId: "p1",
      text: { text: "Plot 5, MIDC, Bhiwandi" },
      mainText: { text: "Plot 5, MIDC" },
      secondaryText: { text: "Bhiwandi" },
      toPlace: () => place,
    };
    class AutocompleteSessionToken {}
    const fetchAutocompleteSuggestions = vi.fn().mockResolvedValue({ suggestions: [{ placePrediction: prediction }] });
    return { AutocompleteSessionToken, AutocompleteSuggestion: { fetchAutocompleteSuggestions }, place };
  }

  it("is disabled while the places library loads", () => {
    render(<PlacesLookup apiKey="k" onPlace={vi.fn()} onFailure={vi.fn()} />);
    expect((screen.getByLabelText("Look up an address") as HTMLInputElement).disabled).toBe(true);
  });

  it("says the lookup is slow to load, in the line that is already there, after the slow-load threshold", () => {
    vi.useFakeTimers();
    render(<PlacesLookup apiKey="k" onPlace={vi.fn()} onFailure={vi.fn()} />);
    expect(screen.getByText(/Check them before you save/)).toBeDefined();
    advance(SLOW_LOAD_MS);
    expect(screen.getByText(/Loading address lookup is taking longer than usual/)).toBeDefined();
    expect(screen.queryByText(/Check them before you save/)).toBeNull();
  });

  it("never says it is slow once the library is there", () => {
    vi.useFakeTimers();
    state.places = fakePlaces();
    render(<PlacesLookup apiKey="k" onPlace={vi.fn()} onFailure={vi.fn()} />);
    advance(SLOW_LOAD_MS);
    expect(screen.queryByText(/taking longer than usual/)).toBeNull();
  });

  it("gives up on the lookup when the script never answers, so the form falls back to the manual fields", () => {
    vi.useFakeTimers();
    state.status = "LOADING";
    const onFailure = vi.fn();
    render(<PlacesLookup apiKey="k" onPlace={vi.fn()} onFailure={onFailure} />);
    advance(SCRIPT_HANG_MS);
    expect(onFailure).toHaveBeenCalledWith("script_error");
  });

  it("searches as you type and hands the form the chosen place's address and micro-degree position", async () => {
    const user = userEvent.setup();
    const lib = fakePlaces();
    state.places = lib;
    const onPlace = vi.fn();
    render(<PlacesLookup apiKey="k" onPlace={onPlace} onFailure={vi.fn()} />);

    await user.type(screen.getByLabelText("Look up an address"), "plot 5");
    await user.click(await screen.findByRole("option", { name: /Plot 5, MIDC/ }));

    await waitFor(() =>
      expect(onPlace).toHaveBeenCalledWith({
        address: "Plot 5, MIDC, Bhiwandi, Maharashtra",
        lat_micro: 19_301_235,
        lng_micro: 73_061_235,
      }),
    );
  });

  it("says so, and offers the manual fields, when the chosen place has no usable position", async () => {
    const user = userEvent.setup();
    const lib = fakePlaces();
    lib.place.location = null as unknown as typeof lib.place.location;
    state.places = lib;
    const onPlace = vi.fn();
    render(<PlacesLookup apiKey="k" onPlace={onPlace} onFailure={vi.fn()} />);

    await user.type(screen.getByLabelText("Look up an address"), "plot 5");
    await user.click(await screen.findByRole("option", { name: /Plot 5, MIDC/ }));
    expect(await screen.findByText(/has no usable position/)).toBeDefined();
    expect(onPlace).not.toHaveBeenCalled();
  });

  it("tells the form the lookup is unavailable when Google refuses the details call", async () => {
    const user = userEvent.setup();
    const lib = fakePlaces();
    lib.place.fetchFields.mockRejectedValue(new Error("REQUEST_DENIED"));
    state.places = lib;
    const onFailure = vi.fn();
    render(<PlacesLookup apiKey="k" onPlace={vi.fn()} onFailure={onFailure} />);

    await user.type(screen.getByLabelText("Look up an address"), "plot 5");
    await user.click(await screen.findByRole("option", { name: /Plot 5, MIDC/ }));
    await waitFor(() => expect(onFailure).toHaveBeenCalledWith("script_error"));
  });

  it("shows the control's own error when the suggestion request is refused, and does not close the form", async () => {
    const user = userEvent.setup();
    const lib = fakePlaces();
    lib.AutocompleteSuggestion.fetchAutocompleteSuggestions.mockRejectedValue(new Error("OVER_QUERY_LIMIT"));
    state.places = lib;
    render(<PlacesLookup apiKey="k" onPlace={vi.fn()} onFailure={vi.fn()} />);

    await user.type(screen.getByLabelText("Look up an address"), "plot 5");
    expect(await screen.findByText("Couldn't look up addresses.")).toBeDefined();
  });
});
