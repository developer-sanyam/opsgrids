import { afterEach, describe, expect, it, vi } from "vitest";

import { getMapsApiKey, mapsEnabled } from "./mapsConfig";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("getMapsApiKey / mapsEnabled", () => {
  it("is disabled when the variable is absent", () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", undefined);
    expect(getMapsApiKey()).toBeNull();
    expect(mapsEnabled()).toBe(false);
  });

  it("is disabled when it is blank or only whitespace", () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
    expect(mapsEnabled()).toBe(false);
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "   ");
    expect(getMapsApiKey()).toBeNull();
  });

  it("returns the trimmed key when one is set", () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", " test-key ");
    expect(getMapsApiKey()).toBe("test-key");
    expect(mapsEnabled()).toBe(true);
  });
});
