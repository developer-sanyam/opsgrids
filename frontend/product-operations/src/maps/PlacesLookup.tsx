import { useMemo, useState } from "react";
import { useMapsLibrary } from "@vis.gl/react-google-maps";

import type { ReportReason } from "@/lib/clientReport";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { MapShell } from "./MapShell";
import { createPlacesAdapter } from "./places";
import type { PlaceSelection } from "./places";

const COPY = {
  idle: "Start typing the warehouse's address.",
  short: "Keep typing — at least three characters.",
  noMatches: (query: string) => `No address matches "${query}".`,
  noMatchesHint: "Try the street and town, or enter the coordinates below.",
  error: "Couldn't look up addresses.",
};

const HELPER = "Fills the location and the coordinates. Check them before you save.";
/** Said in the helper line while the lookup is slow to load: that line is already there, so nothing moves. */
const SLOW_HELPER = "Loading address lookup is taking longer than usual. You can enter the coordinates below instead.";

interface PlacesLookupProps {
  apiKey: string;
  /** The chosen place's address and position. The form decides what to do with them. */
  onPlace: (place: PlaceSelection) => void;
  onFailure: (reason: Extract<ReportReason, "script_error" | "auth_failure">) => void;
}

/**
 * "Look up an address": a `Combobox` fed by the Places data API. Choosing a suggestion hands the
 * form its formatted address and position; the manual latitude and longitude fields beside it stay
 * the accessible fallback and the only path in a build without a key.
 */
export default function PlacesLookup({ apiKey, onPlace, onFailure }: PlacesLookupProps) {
  return (
    <MapShell apiKey={apiKey} onFailure={onFailure}>
      <Lookup onPlace={onPlace} onFailure={onFailure} />
    </MapShell>
  );
}

function Lookup({ onPlace, onFailure }: Pick<PlacesLookupProps, "onPlace" | "onFailure">) {
  const places = useMapsLibrary("places");
  const adapter = useMemo(() => (places ? createPlacesAdapter(places) : null), [places]);
  const [resolveError, setResolveError] = useState<string | undefined>(undefined);
  const slow = useSlowLoad(adapter === null);

  async function pick(option: ComboboxOption | null) {
    if (!option || !adapter) return;
    setResolveError(undefined);
    try {
      const place = await adapter.resolve(option.id);
      if (place) onPlace(place);
      else setResolveError("That address has no usable position. Pick another, or enter the coordinates below.");
    } catch {
      // A refused call (Places API not enabled for the key, quota) reads as a lookup failure; the
      // manual fields remain. Nothing from the error is kept — it can carry the request URL.
      onFailure("script_error");
    }
  }

  return (
    <Combobox
      label="Look up an address"
      value={null}
      onChange={(option) => void pick(option)}
      loadOptions={(query, signal) => (adapter ? adapter.search(query, signal) : Promise.resolve([]))}
      minQueryLength={3}
      cap={5}
      placeholder={adapter ? "Search for the warehouse's address…" : "Loading address lookup…"}
      disabled={adapter === null}
      copy={COPY}
      helper={slow ? SLOW_HELPER : HELPER}
      {...(resolveError !== undefined ? { error: resolveError } : {})}
    />
  );
}
