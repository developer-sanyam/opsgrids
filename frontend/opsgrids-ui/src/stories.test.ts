/// <reference types="vite/client" />
/**
 * Every exported component has a story.
 *
 * The workshop is also the visual and accessibility regression suite (`opsgrids-visual`
 * photographs and axe-checks every story), so a component with no story is a component with no
 * safety net — and the redesign is about to change every one of them. A story counts a component
 * as covered when its meta names it as `component` or in `subcomponents`.
 */

import { describe, expect, it } from "vitest";

import * as icons from "./icons";
import * as ui from "./index";

interface StoryModule {
  default: { title?: string; component?: unknown; subcomponents?: Record<string, unknown> };
}

const modules = import.meta.glob<StoryModule>("./**/*.stories.tsx", { eager: true });

function exportedComponents(): string[] {
  return Object.entries(ui)
    .filter(([name, value]) => /^[A-Z]/.test(name) && typeof value === "function" && !(name in icons))
    .map(([name]) => name);
}

describe("the workshop", () => {
  const metas = Object.values(modules).map((module) => module.default);
  const covered = new Set<unknown>(
    metas.flatMap((meta) => [meta.component, ...Object.values(meta.subcomponents ?? {})]),
  );

  it("has a story for every component @opsgrids/ui exports", () => {
    const missing = exportedComponents().filter((name) => !covered.has(ui[name as keyof typeof ui]));
    expect(missing).toEqual([]);
  });

  it("shows every icon", () => {
    expect(metas.some((meta) => meta.title === "Foundations/Icons")).toBe(true);
  });
});
