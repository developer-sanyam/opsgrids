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

import type {
  PageDoc,
  SectionDef,
  SectionInstance,
  SettingValue,
  StoreDoc,
  ThemeTokens,
} from "@/theme/schema";

import type { DeviceKey } from "@/ui/deviceWidths";

import { bottomRow, clampRect, resolveCollisions } from "./grid";
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
  | { type: "add-section"; def: SectionDef; id: string; at?: { x: number; y: number } }
  | { type: "move-section"; id: string; x: number; y: number }
  | { type: "resize-section"; id: string; w: number; h: number }
  | { type: "remove-section"; id: string }
  | { type: "duplicate-section"; id: string; newId: string }
  | { type: "reorder-depth"; id: string; direction: "forward" | "backward" }
  | { type: "set-setting"; id: string; key: string; value: SettingValue }
  | { type: "set-token"; key: keyof ThemeTokens; value: string | number }
  | { type: "set-brand"; key: keyof StoreDoc["brand"]; value: string }
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

/** Clamp a section's rectangle, then let the push-down rule settle whatever it displaced. */
function place(sections: SectionInstance[], id: string, rect: Partial<SectionInstance["layout"]>) {
  const target = sections.find((section) => section.id === id);
  if (!target) return sections;
  const layout = clampRect({ ...target.layout, ...rect });
  const next = sections.map((section) =>
    section.id === id ? { ...section, layout } : section,
  );
  return resolveCollisions(next, id);
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

    case "add-section": {
      const page = currentPage(state);
      if (!page) return state;
      const at = action.at ?? { x: 0, y: bottomRow(page.sections) };
      const instance = createInstance(action.def, action.id, at);
      const next = withSections(state.present, state.pageId, (sections) =>
        resolveCollisions(
          [...sections, { ...instance, layout: clampRect(instance.layout) }],
          action.id,
        ),
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
        return resolveCollisions(inserted, action.newId);
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
        return sections.map((section) =>
          section.id === action.id
            ? { ...section, settings: { ...section.settings, [action.key]: action.value } }
            : section,
        );
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

    case "set-brand": {
      if (state.present.brand[action.key] === action.value) return state;
      const next: StoreDoc = {
        ...state.present,
        brand: { ...state.present.brand, [action.key]: action.value },
      };
      return commit(state, next, `brand:${action.key}`);
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
