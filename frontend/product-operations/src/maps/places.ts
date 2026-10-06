/**
 * Address lookup over Google's Places *data* API (`AutocompleteSuggestion`), not the legacy
 * autocomplete widget: the suggestions come back as data and are rendered by the design system's
 * own `Combobox`, so the control keeps one keyboard contract and one look. Nothing here touches the
 * DOM or the Maps components, which keeps the adapter testable with a fake library.
 *
 * One session token spans a typing session, from the first query to the chosen suggestion, so
 * Google bills the lookup as one session rather than per keystroke; a new token starts after every
 * pick.
 */
import type { ComboboxOption } from "@/ui";
import { degreesToMicro, isValidMicroPair } from "@/lib/geo";

export interface PlaceSelection {
  /** Google's formatted address, for the warehouse's `location` field. */
  address: string;
  lat_micro: number;
  lng_micro: number;
}

export interface PlacesAdapter {
  search: (query: string, signal: AbortSignal) => Promise<ComboboxOption[]>;
  /** The chosen suggestion's address and position, or `null` when Google returned no usable position. */
  resolve: (suggestionId: string) => Promise<PlaceSelection | null>;
}

type Prediction = google.maps.places.PlacePrediction;

export function createPlacesAdapter(places: google.maps.PlacesLibrary): PlacesAdapter {
  let token = new places.AutocompleteSessionToken();
  const predictions = new Map<string, Prediction>();

  return {
    async search(query, signal) {
      const { suggestions } = await places.AutocompleteSuggestion.fetchAutocompleteSuggestions({
        input: query,
        sessionToken: token,
      });
      // The control already abandoned this query; do not let it overwrite a newer one's cache.
      if (signal.aborted) return [];
      predictions.clear();
      const options: ComboboxOption[] = [];
      for (const suggestion of suggestions) {
        const prediction = suggestion.placePrediction;
        if (!prediction) continue;
        predictions.set(prediction.placeId, prediction);
        options.push({
          id: prediction.placeId,
          label: prediction.mainText?.text ?? prediction.text.text,
          sublabel: prediction.secondaryText?.text,
        });
      }
      return options;
    },

    async resolve(suggestionId) {
      const prediction = predictions.get(suggestionId);
      if (!prediction) return null;
      const place = prediction.toPlace();
      await place.fetchFields({ fields: ["formattedAddress", "location"] });
      // The session ends with the pick, whether or not the details were usable.
      token = new places.AutocompleteSessionToken();
      predictions.clear();
      const location = place.location;
      if (!location) return null;
      const lat_micro = degreesToMicro(location.lat());
      const lng_micro = degreesToMicro(location.lng());
      if (!isValidMicroPair(lat_micro, lng_micro)) return null;
      return { address: place.formattedAddress ?? "", lat_micro, lng_micro };
    },
  };
}
