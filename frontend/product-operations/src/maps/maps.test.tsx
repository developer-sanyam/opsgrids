/**
 * The three Maps components, against a stand-in for `@vis.gl/react-google-maps` (the real one needs
 * Google's script and a browser). What is pinned here is what this app decides: which sites get a
 * pin, what the popup says, that a loader error or an auth refusal tells the page once and with no
 * detail, that the key goes to the provider and nowhere else, and that the places lookup fills the
 * form from a chosen suggestion.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import type { WarehouseMapSiteOut } from "@/api/types";
import LocationPinMap from "./LocationPinMap";
import { MapShell } from "./MapShell";
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
