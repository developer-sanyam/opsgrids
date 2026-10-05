import { beforeEach, describe, expect, it, vi } from "vitest";

import { reportOnce, resetReports } from "./clientReport";

describe("reportOnce", () => {
  beforeEach(() => {
    resetReports();
  });

  it("reports each failure type once per session, however often it fails", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    reportOnce("maps_load_failed", "script_error");
    reportOnce("maps_load_failed", "auth_failure");
    reportOnce("floor_render_failed", "render_error");
    reportOnce("webgl_unavailable", "no_webgl");
    reportOnce("webgl_unavailable", "no_webgl");
    expect(warn).toHaveBeenCalledTimes(3);
    expect(warn.mock.calls.map((call) => String(call[0]))).toEqual([
      "[operations] maps_load_failed (script_error)",
      "[operations] floor_render_failed (render_error)",
      "[operations] webgl_unavailable (no_webgl)",
    ]);
  });

  it("carries a fixed kind and reason and nothing else — no key, no coordinates, no thrown error", () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "AIza-secret-key");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    reportOnce("maps_load_failed", "script_error");
    const args = warn.mock.calls.flat();
    expect(args).toHaveLength(1);
    expect(JSON.stringify(args)).not.toContain("AIza-secret-key");
    expect(JSON.stringify(args)).not.toMatch(/\d{2}\.\d{4}/);
    vi.unstubAllEnvs();
  });
});
