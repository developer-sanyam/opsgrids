/** Test-only components that watch and move the router, so a test can see and change where it is. */

import { useLocation, useNavigate } from "react-router-dom";

/** Shows where the router is, so a test can see that a value never reached the URL. */
export function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</p>;
}

/** A way to move to another record without leaving the screen's component. */
export function GoTo({ path }: { path: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => void navigate(path)}>
      {`Go to ${path}`}
    </button>
  );
}
