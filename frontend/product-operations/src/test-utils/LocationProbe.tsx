import { useLocation } from "react-router-dom";

/** Writes the router's location into the DOM, where a test can read it without touching the router. */
export function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div hidden data-testid="location" data-pathname={pathname} data-search={search} />;
}
