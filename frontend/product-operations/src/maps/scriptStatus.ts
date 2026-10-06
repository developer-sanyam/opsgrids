import { APILoadingStatus } from "@vis.gl/react-google-maps";

/** How long Google's script may stay pending before the map is given up on and the list takes over.
 *  A blocked or offline script errors on its own; one that never answers (a captive portal that
 *  returns a page, a stalled connection) says nothing, so this is the only thing that ends the wait.
 *  Well past `SLOW_LOAD_MS`, which is when the person is told it is slow. */
export const SCRIPT_HANG_MS = 30_000;

/** The provider has not finished: not started yet, or the script is still on its way. */
export function isScriptPending(status: APILoadingStatus): boolean {
  return status === APILoadingStatus.NOT_LOADED || status === APILoadingStatus.LOADING;
}
