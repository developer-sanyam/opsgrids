/**
 * What the harness photographs: every story the workshop build indexed, and the app pages listed
 * in `pages.ts`. The spec files, the global teardown's orphan check and the a11y baseline all read
 * the same list, so "which baselines should exist" has one answer.
 */

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { THEMES, WIDTHS } from "./env";
import type { Theme, Width } from "./env";
import { PAGES } from "./pages";

const STORY_INDEX = fileURLToPath(
  new URL("../../opsgrids-ui/storybook-static/index.json", import.meta.url),
);

export interface Story {
  /** Storybook's id: `actions-button--primary`. */
  id: string;
  title: string;
  name: string;
  tags: string[];
}

interface IndexEntry {
  type: string;
  id: string;
  title: string;
  name: string;
  tags?: string[];
}

/**
 * The stories in the last workshop build. `pnpm visual:check` builds the workshop first; running
 * Playwright on its own against a stale build would silently test yesterday's stories.
 */
export function loadStories(): Story[] {
  if (!existsSync(STORY_INDEX)) {
    throw new Error(
      `No workshop build at ${STORY_INDEX}. Run \`pnpm visual:check\` (it builds the workshop and ` +
        "the apps first), or `pnpm --filter @opsgrids/ui build-storybook`.",
    );
  }
  const index = JSON.parse(readFileSync(STORY_INDEX, "utf8")) as {
    entries: Record<string, IndexEntry>;
  };
  return Object.values(index.entries)
    .filter((entry) => entry.type === "story")
    .map(({ id, title, name, tags }) => ({ id, title, name, tags: tags ?? [] }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** `story/actions-button--primary`, `page/crm/contacts`. Also the baseline directory. */
export function storySurface(story: Story): string {
  return `story/${story.id}`;
}

/** The screenshot's name, as path segments under `baselines/<platform>/`. */
export function snapshotName(surface: string, theme: Theme, width: Width): string[] {
  return [...surface.split("/"), `${theme}-${width}.png`];
}

/** The a11y baseline's key for one rendering of one surface. */
export function a11yKey(surface: string, theme: Theme, width: Width): string {
  return `${surface}|${theme}|${width}`;
}

/** Every surface, in every theme at every width: the full set of renderings a run covers. */
export function allRenderings(): { surface: string; theme: Theme; width: Width }[] {
  const surfaces = [...loadStories().map(storySurface), ...PAGES.map((page) => page.surface)];
  return surfaces.flatMap((surface) =>
    THEMES.flatMap((theme) => WIDTHS.map((width) => ({ surface, theme, width }))),
  );
}
