/**
 * One line per failure type per session for the three things that can quietly fail in a user's
 * browser here: Google Maps not loading, the floor plan not rendering, WebGL being unavailable.
 *
 * This app has no error-reporting service, so the report is a `console.warn` — which is also what a
 * support engineer asks a user to open. The line carries a fixed kind and a fixed reason code and
 * nothing else: not the thrown error (a loader error can embed the script URL, and the URL embeds
 * the Maps key) and never a coordinate.
 */

export type ReportKind = "maps_load_failed" | "floor_render_failed" | "webgl_unavailable";
export type ReportReason = "auth_failure" | "script_error" | "render_error" | "no_webgl";

const reported = new Set<ReportKind>();

/** Emits the report the first time `kind` fails in this page session; later calls are silent. */
export function reportOnce(kind: ReportKind, reason: ReportReason): void {
  if (reported.has(kind)) return;
  reported.add(kind);
  console.warn(`[operations] ${kind} (${reason})`);
}

/** Forgets what was reported. Only a test needs this. */
export function resetReports(): void {
  reported.clear();
}
