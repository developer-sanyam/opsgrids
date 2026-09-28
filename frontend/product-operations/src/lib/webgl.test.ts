import { afterEach, describe, expect, it, vi } from "vitest";

import { hasWebGLSupport } from "./webgl";

describe("hasWebGLSupport", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("is false when the canvas cannot produce a webgl2 or webgl context", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    expect(hasWebGLSupport()).toBe(false);
  });

  it("is true when webgl2 is available", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((kind: string) =>
      kind === "webgl2" ? ({} as WebGL2RenderingContext) : null,
    );
    expect(hasWebGLSupport()).toBe(true);
  });

  it("falls back to webgl when webgl2 is unavailable", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((kind: string) =>
      kind === "webgl" ? ({} as WebGLRenderingContext) : null,
    );
    expect(hasWebGLSupport()).toBe(true);
  });

  it("is false when the browser throws instead of returning null (a blocklisted context)", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => {
      throw new Error("WebGL is disabled");
    });
    expect(hasWebGLSupport()).toBe(false);
  });
});
