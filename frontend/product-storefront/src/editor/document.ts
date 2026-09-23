/**
 * The editor's document state: one reducer, every mutation, undo and redo.
 *
 * All of it lives here rather than in the panes because history is the thing most canvas editors
 * get wrong, and a rule you cannot call in a test is a rule you cannot keep. The reducer is pure
 * — ids for new sections are supplied by the caller, never generated inside — so a whole editing
 * session can be replayed in `document.test.ts` without a DOM.
 *
 * Dirty is `present !== saved` by reference. Every mutation builds a new document and undo
 * restores the exact object it replaced, so undoing back to the last save is byte-identical *and*
 * reference-identical: the indicator clears on its own, with no deep compare on every keystroke.
 */

import { bool } from "@/theme/render/settings";
import { BLEED_SETTING } from "@/theme/schema";
import { navPlacement } from "@/theme/store";
import type {
  NavPlacement,
  PageDoc,
  SectionDef,
  SectionInstance,
  SettingValue,
  StoreDoc,
  ThemeTokens,
} from "@/theme/schema";

import type { DeviceKey } from "@/ui/deviceWidths";

import { bottomRow, clampRect, COLUMNS, resolveCollisions } from "./grid";
import { checkRoute, isStructuralRoute, nameFromRoute, normalisePageName } from "./pageRoutes";
import { createInstance } from "./sectionLibrary";

/** The preview widths are the app's, shared with the buyer-facing preview screens. */
export type Device = DeviceKey;

/** How far back undo goes. Deep enough for a session, bounded so a long one cannot grow forever. */
export const HISTORY_LIMIT = 100;

export interface EditorState {
  past: StoreDoc[];
  present: StoreDoc;
  future: StoreDoc[];
  /** The document as the server last confirmed it. */
  saved: StoreDoc;
  pageId: string;
  selectedId: string | null;
  device: Device;
  /**
   * Which run of coalescing edits `present` belongs to. Typing a heading is one undo step, not
   * one per character; dragging a slider is one step, not forty.
   */
  mergeKey: string | null;
}

export type EditorAction =
  | { type: "select"; id: string | null }
  | { type: "set-device"; device: Device }
  | { type: "set-page"; pageId: string }
  | { type: "add-page"; id: string; route: string; name?: string; nav?: NavPlacement }
  | { type: "rename-page"; pageId: string; name: string }
  | { type: "set-page-route"; pageId: string; route: string }
  | { type: "set-page-nav"; pageId: string; nav: NavPlacement }
  | { type: "delete-page"; pageId: string }
  | { type: "reorder-pages"; pageId: string; direction: "up" | "down" }
  | { type: "add-section"; def: SectionDef; id: string; at?: { x: number; y: number } }
  | { type: "move-section"; id: string; x: number; y: number }
  | { type: "resize-section"; id: string; w: number; h: number }
  | { type: "remove-section"; id: string }
  | { type: "duplicate-section"; id: string; newId: string }
  | { type: "reorder-depth"; id: string; direction: "forward" | "backward" }
  | { type: "set-setting"; id: string; key: string; value: SettingValue }
  | { type: "set-token"; key: keyof ThemeTokens; value: string | number }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "saved"; store: StoreDoc }
  | { type: "discard" };

export function initEditor(store: StoreDoc, pageId?: string): EditorState {
  return {
    past: [],
    present: store,
    future: [],
    saved: store,
    pageId: pageId ?? store.pages[0]?.id ?? "",
    selectedId: null,
    device: "desktop",
    mergeKey: null,
  };
}

export function isDirty(state: EditorState): boolean {
  return state.present !== state.saved;
}

export function canUndo(state: EditorState): boolean {
  return state.past.length > 0;
}

export function canRedo(state: EditorState): boolean {
  return state.future.length > 0;
}

export function currentPage(state: EditorState): PageDoc | undefined {
  return state.present.pages.find((page) => page.id === state.pageId);
}

export function selectedSection(state: EditorState): SectionInstance | undefined {
  if (state.selectedId === null) return undefined;
  return currentPage(state)?.sections.find((section) => section.id === state.selectedId);
}

/* ------------------------------- immutable edits ------------------------------- */

function withSections(
  store: StoreDoc,
  pageId: string,
  fn: (sections: SectionInstance[]) => SectionInstance[],
): StoreDoc {
  const page = store.pages.find((candidate) => candidate.id === pageId);
  if (!page) return store;
  const sections = fn(page.sections);
  if (sections === page.sections) return store;
  return {
    ...store,
    pages: store.pages.map((candidate) =>
      candidate.id === pageId ? { ...candidate, sections } : candidate,
    ),
  };
}

/**
 * Replace the page list, keeping the document identical when nothing moved.
 *
 * The identity check is not an optimisation: `commit` treats `next === present` as "no edit", so
 * an action the rules refuse — a duplicate route, a locked page — costs no undo step and leaves
 * the dirty indicator alone. Refusals are silent *in the reducer* on purpose; the page manager
 * validates first and is the thing that explains, because a reducer has nowhere to say it.
 */
function withPages(store: StoreDoc, fn: (pages: PageDoc[]) => PageDoc[]): StoreDoc {
  const pages = fn(store.pages);
  return pages === store.pages ? store : { ...store, pages };
}

/** One page rewritten, or the same array when the change was a no-op. */
function patchPage(pages: PageDoc[], pageId: string, fn: (page: PageDoc) => PageDoc): PageDoc[] {
  const index = pages.findIndex((page) => page.id === pageId);
  const current = pages[index];
  if (index === -1 || !current) return pages;
  const next = fn(current);
  if (next === current) return pages;
  return pages.map((page) => (page.id === pageId ? next : page));
}

/**
 * Which page the editor lands on once `pageId` is gone.
 *
 * The one after it, or the one before when it was last. Leaving the editor pointed at a page that
 * no longer exists drops it into the "this store has no pages" screen with a document that plainly
 * has pages, which reads as data loss at exactly the moment a merchant is least sure.
 */
export function neighbourPageId(pages: PageDoc[], pageId: string): string {
  const index = pages.findIndex((page) => page.id === pageId);
  if (index === -1) return pages[0]?.id ?? "";
  return pages[index + 1]?.id ?? pages[index - 1]?.id ?? "";
}

/**
 * A bleeding section occupies all twelve columns — in the document, not only on screen.
 *
 * `PageRenderer` places a section carrying `BLEED_SETTING` across the full canvas and ignores its
 * `x` and `w` entirely, while `resolveCollisions` settles overlaps *on* `x` and `w`. Left
 * disagreeing, two sections sharing rows — one bleeding, one narrow — pass the editor's collision
 * check untouched and then overlap on the live store, which is a layout defect a merchant can only
 * discover by publishing.
 *
 * Normalising the rectangle to what the store will actually render makes the two agree with no new
 * rule in the geometry: collision resolution keeps comparing plain rectangles, and the canvas, the
 * announcement a keyboard user hears and the saved document all describe the same shape.
 *
 * This belongs to the document layer rather than to `grid.ts` because it is a fact about a
 * section's *settings*, which the geometry deliberately knows nothing about — and because toggling
 * the setting is itself an edit (`set-setting`) that must widen and re-settle, something a
 * collision resolver is never called for.
 *
 * `bool` is the page layer's own tolerant reader, so a document that stored `"true"` as a string
 * bleeds here exactly as it bleeds there.
 */
function withBleedSpans(sections: SectionInstance[]): SectionInstance[] {
  let changed = false;
  const next = sections.map((section) => {
    if (!bool(section.settings, BLEED_SETTING, false)) return section;
    if (section.layout.x === 0 && section.layout.w === COLUMNS) return section;
    changed = true;
    return { ...section, layout: { ...section.layout, x: 0, w: COLUMNS } };
  });
  return changed ? next : sections;
}

/**
 * Widen whatever bleeds, then push down whatever that now overlaps.
 *
 * The order is the whole point: widening after resolving would hand back a document with the
 * overlap still in it. Every mutation that can move, size or re-flag a section goes through here.
 */
function settle(sections: SectionInstance[], anchorId: string): SectionInstance[] {
  return resolveCollisions(withBleedSpans(sections), anchorId);
}

/** Clamp a section's rectangle, then let the push-down rule settle whatever it displaced. */
function place(sections: SectionInstance[], id: string, rect: Partial<SectionInstance["layout"]>) {
  const target = sections.find((section) => section.id === id);
  if (!target) return sections;
  const layout = clampRect({ ...target.layout, ...rect });
  const next = sections.map((section) =>
    section.id === id ? { ...section, layout } : section,
  );
  return settle(next, id);
}

function commit(state: EditorState, next: StoreDoc, mergeKey: string | null = null): EditorState {
  if (next === state.present) return state;
  const merging = mergeKey !== null && mergeKey === state.mergeKey && state.past.length > 0;
  return {
    ...state,
    past: merging ? state.past : [...state.past, state.present].slice(-HISTORY_LIMIT),
    present: next,
    // Any new edit abandons the redo branch — the standard contract, and the only one that
    // cannot produce a redo that reapplies an edit to a document it was never made against.
    future: [],
    mergeKey,
  };
}

/* ---------------------------------- the reducer --------------------------------- */

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "select":
      if (state.selectedId === action.id) return state;
      return { ...state, selectedId: action.id, mergeKey: null };

    case "set-device":
      return state.device === action.device ? state : { ...state, device: action.device };

    case "set-page": {
      if (state.pageId === action.pageId) return state;
      return { ...state, pageId: action.pageId, selectedId: null, mergeKey: null };
    }

    /*
     * The page actions.
     *
     * Each re-checks the rule it depends on rather than trusting the caller, because the caller is
     * a dialog and the reducer is the only place a rule can be *true*. `checkRoute` is the same
     * function the dialog's field calls, so the message a merchant reads and the reason the
     * document refuses can never drift into two different rules.
     */

    case "add-page": {
      const check = checkRoute(action.route, state.present.pages);
      if (check.error !== null) return state;
      if (state.present.pages.some((page) => page.id === action.id)) return state;
      const name = normalisePageName(action.name ?? "") || nameFromRoute(check.route);
      const page: PageDoc = {
        id: action.id,
        route: check.route,
        name,
        // Omitted, not `undefined`: a page that says nothing about its navigation reads as
        // "primary" through `navPlacement`, which is what a merchant expects of a page they just
        // made — and it keeps the saved document free of fields nobody set.
        ...(action.nav !== undefined ? { nav: action.nav } : {}),
        sections: [],
      };
      const next = withPages(state.present, (pages) => [...pages, page]);
      // Land on the new page: it is empty, and the next thing anyone does is put a section on it.
      return { ...commit(state, next), pageId: action.id, selectedId: null };
    }

    case "rename-page": {
      const name = normalisePageName(action.name);
      // An unnamed page is a blank nav item. Refuse rather than invent a name over the top of what
      // the merchant is part-way through typing.
      if (name === "") return state;
      const next = withPages(state.present, (pages) =>
        patchPage(pages, action.pageId, (page) => (page.name === name ? page : { ...page, name })),
      );
      return commit(state, next, `page-name:${action.pageId}`);
    }

    case "set-page-route": {
      const page = state.present.pages.find((candidate) => candidate.id === action.pageId);
      if (!page) return state;
      // `/`, `/catalogue` and `/product` are link targets other code resolves without the merchant
      // ever having written the link — see `STRUCTURAL_ROUTES`.
      if (isStructuralRoute(page.route)) return state;
      const check = checkRoute(action.route, state.present.pages, action.pageId);
      if (check.error !== null) return state;
      // And nothing may move *onto* one either: a second page answering `/catalogue` would shadow
      // the real one on a store that had somehow lost it, which is the same dead-link failure
      // arriving from the other direction.
      if (isStructuralRoute(check.route)) return state;
      const next = withPages(state.present, (pages) =>
        patchPage(pages, action.pageId, (candidate) =>
          candidate.route === check.route ? candidate : { ...candidate, route: check.route },
        ),
      );
      return commit(state, next, `page-route:${action.pageId}`);
    }

    case "set-page-nav": {
      const next = withPages(state.present, (pages) =>
        patchPage(pages, action.pageId, (page) =>
          navPlacement(page) === action.nav ? page : { ...page, nav: action.nav },
        ),
      );
      return commit(state, next);
    }

    case "delete-page": {
      const page = state.present.pages.find((candidate) => candidate.id === action.pageId);
      if (!page) return state;
      if (isStructuralRoute(page.route)) return state;
      const next = withPages(state.present, (pages) =>
        pages.filter((candidate) => candidate.id !== action.pageId),
      );
      const committed = commit(state, next);
      if (state.pageId !== action.pageId) return committed;
      // Deleting the page being edited moves the editor onto its neighbour, never onto nothing.
      return {
        ...committed,
        pageId: neighbourPageId(state.present.pages, action.pageId),
        selectedId: null,
      };
    }

    case "reorder-pages": {
      // Page order is nav order — there is no second list to keep in step with this one.
      const next = withPages(state.present, (pages) => {
        const index = pages.findIndex((page) => page.id === action.pageId);
        if (index === -1) return pages;
        const target = action.direction === "up" ? index - 1 : index + 1;
        if (target < 0 || target >= pages.length) return pages;
        const reordered = pages.slice();
        const [moved] = reordered.splice(index, 1);
        if (!moved) return pages;
        reordered.splice(target, 0, moved);
        return reordered;
      });
      return commit(state, next);
    }

    case "add-section": {
      const page = currentPage(state);
      if (!page) return state;
      const at = action.at ?? { x: 0, y: bottomRow(page.sections) };
      const instance = createInstance(action.def, action.id, at);
      const next = withSections(state.present, state.pageId, (sections) =>
        settle([...sections, { ...instance, layout: clampRect(instance.layout) }], action.id),
      );
      return { ...commit(state, next), selectedId: action.id };
    }

    case "move-section": {
      const next = withSections(state.present, state.pageId, (sections) =>
        place(sections, action.id, { x: action.x, y: action.y }),
      );
      return commit(state, next, `move:${action.id}`);
    }

    case "resize-section": {
      const next = withSections(state.present, state.pageId, (sections) =>
        place(sections, action.id, { w: action.w, h: action.h }),
      );
      return commit(state, next, `resize:${action.id}`);
    }

    case "remove-section": {
      const next = withSections(state.present, state.pageId, (sections) => {
        const kept = sections.filter((section) => section.id !== action.id);
        return kept.length === sections.length ? sections : kept;
      });
      const committed = commit(state, next);
      return state.selectedId === action.id
        ? { ...committed, selectedId: null }
        : committed;
    }

    case "duplicate-section": {
      const page = currentPage(state);
      const source = page?.sections.find((section) => section.id === action.id);
      if (!source) return state;
      const copy: SectionInstance = {
        ...source,
        id: action.newId,
        settings: { ...source.settings },
        layout: clampRect({ ...source.layout, y: source.layout.y + source.layout.h }),
      };
      const next = withSections(state.present, state.pageId, (sections) => {
        const index = sections.findIndex((section) => section.id === action.id);
        const inserted = [...sections.slice(0, index + 1), copy, ...sections.slice(index + 1)];
        return settle(inserted, action.newId);
      });
      return { ...commit(state, next), selectedId: action.newId };
    }

    case "reorder-depth": {
      const next = withSections(state.present, state.pageId, (sections) => {
        const index = sections.findIndex((section) => section.id === action.id);
        if (index === -1) return sections;
        const target = action.direction === "forward" ? index + 1 : index - 1;
        if (target < 0 || target >= sections.length) return sections;
        const reordered = sections.slice();
        const [moved] = reordered.splice(index, 1);
        if (!moved) return sections;
        reordered.splice(target, 0, moved);
        return reordered;
      });
      return commit(state, next);
    }

    case "set-setting": {
      const next = withSections(state.present, state.pageId, (sections) => {
        const target = sections.find((section) => section.id === action.id);
        if (!target) return sections;
        if (target.settings[action.key] === action.value) return sections;
        const updated = sections.map((section) =>
          section.id === action.id
            ? { ...section, settings: { ...section.settings, [action.key]: action.value } }
            : section,
        );
        // Turning bleed on is a layout change as much as a settings one: the section grows to the
        // full canvas there and then, and whatever that now covers is pushed down.
        return settle(updated, action.id);
      });
      return commit(state, next, `setting:${action.id}:${action.key}`);
    }

    case "set-token": {
      if (state.present.tokens[action.key] === action.value) return state;
      const next: StoreDoc = {
        ...state.present,
        tokens: { ...state.present.tokens, [action.key]: action.value },
      };
      return commit(state, next, `token:${action.key}`);
    }

    case "undo": {
      const previous = state.past.at(-1);
      if (previous === undefined) return state;
      return {
        ...state,
        past: state.past.slice(0, -1),
        present: previous,
        future: [state.present, ...state.future],
        mergeKey: null,
      };
    }

    case "redo": {
      const [next, ...rest] = state.future;
      if (next === undefined) return state;
      return {
        ...state,
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        present: next,
        future: rest,
        mergeKey: null,
      };
    }

    case "saved":
      // The baseline is the document that was *sent*, not whatever `present` holds now: a save
      // takes a round trip, and an edit made while it was in flight is still unsaved. History
      // survives a save too — undoing past the save point is legitimate, and the dirty indicator
      // comes back on when it happens.
      return { ...state, saved: action.store, mergeKey: null };

    case "discard": {
      if (state.present === state.saved) return state;
      // Discard goes through `commit` like any other edit, so it is itself undoable. Throwing
      // away an afternoon's work behind a confirm dialog with no way back is how editors lose
      // people's trust; here Undo brings it straight back.
      return { ...commit(state, state.saved), selectedId: null };
    }

    default:
      return state;
  }
}
