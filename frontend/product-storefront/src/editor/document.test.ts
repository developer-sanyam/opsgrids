import { describe, expect, it } from "vitest";

import type { SectionDef, StoreDoc } from "@/theme/schema";

import {
  canRedo,
  canUndo,
  currentPage,
  editorReducer,
  initEditor,
  isDirty,
  selectedSection,
} from "./document";
import type { EditorAction, EditorState } from "./document";

/**
 * The reducer with no DOM anywhere near it.
 *
 * Undo/redo, collision settling and the dirty flag are the three things a canvas editor is most
 * likely to get subtly wrong, and all three are decided here — so they are pinned here, as a
 * sequence of edits replayed exactly the way a session would produce them.
 */

const HERO: SectionDef = {
  type: "hero",
  name: "Hero",
  group: "content",
  defaultLayout: { w: 12, h: 6 },
  fields: [
    { key: "heading", label: "Heading", type: "text", default: "Hello" },
    { key: "image", label: "Image", type: "image", default: null },
  ],
};

const NOTE: SectionDef = {
  type: "note",
  name: "Note",
  group: "content",
  defaultLayout: { w: 6, h: 3 },
  fields: [{ key: "body", label: "Body", type: "textarea", default: "" }],
};

function store(): StoreDoc {
  return {
    themeKey: "depot",
    brand: { name: "Verma Traders", tagline: "Since 1994", logoText: "VT" },
    tokens: {
      colorBg: "#ffffff",
      colorSurface: "#f6f6f6",
      colorText: "#111111",
      colorMuted: "#666666",
      colorAccent: "#1d4ed8",
      colorAccentText: "#ffffff",
      colorBorder: "#e4e4e4",
      fontHeading: "Inter, sans-serif",
      fontBody: "Inter, sans-serif",
      radius: 8,
      space: 16,
      maxWidth: 1200,
    },
    pages: [
      { id: "home", route: "/", name: "Home", sections: [] },
      { id: "about", route: "/about", name: "About", sections: [] },
    ],
  };
}

function run(state: EditorState, ...actions: EditorAction[]): EditorState {
  return actions.reduce(editorReducer, state);
}

function sections(state: EditorState) {
  return currentPage(state)?.sections ?? [];
}

describe("adding sections", () => {
  it("lands a new section at the bottom with every declared default", () => {
    const state = run(initEditor(store()), { type: "add-section", def: HERO, id: "s1" });
    const added = sections(state)[0];
    expect(added?.layout).toEqual({ x: 0, y: 0, w: 12, h: 6 });
    expect(added?.settings).toEqual({ heading: "Hello", image: null });
    expect(state.selectedId).toBe("s1");
  });

  it("stacks the next one below the last, never on top of it", () => {
    const state = run(
      initEditor(store()),
      { type: "add-section", def: HERO, id: "s1" },
      { type: "add-section", def: NOTE, id: "s2" },
    );
    expect(sections(state)[1]?.layout.y).toBe(6);
  });

  it("clamps a section dropped past the right edge back inside the grid", () => {
    const state = run(initEditor(store()), {
      type: "add-section",
      def: NOTE,
      id: "s1",
      at: { x: 11, y: 0 },
    });
    expect(sections(state)[0]?.layout.x).toBe(6);
  });

  it("only touches the page being edited", () => {
    const state = run(initEditor(store()), { type: "add-section", def: HERO, id: "s1" });
    expect(state.present.pages[1]?.sections).toHaveLength(0);
  });
});

describe("moving and resizing", () => {
  const base = run(
    initEditor(store()),
    { type: "add-section", def: NOTE, id: "a", at: { x: 0, y: 0 } },
    { type: "add-section", def: NOTE, id: "b", at: { x: 0, y: 3 } },
  );

  it("pushes what it lands on down rather than overlapping it", () => {
    const state = run(base, { type: "move-section", id: "b", x: 0, y: 0 });
    expect(sections(state).find((entry) => entry.id === "b")?.layout.y).toBe(0);
    expect(sections(state).find((entry) => entry.id === "a")?.layout.y).toBe(3);
  });

  it("refuses to resize below one column or one row", () => {
    const state = run(base, { type: "resize-section", id: "a", w: 0, h: -5 });
    expect(sections(state)[0]?.layout).toMatchObject({ w: 1, h: 1 });
  });

  it("coalesces a run of moves on one section into a single undo step", () => {
    const state = run(
      base,
      { type: "move-section", id: "a", x: 1, y: 0 },
      { type: "move-section", id: "a", x: 2, y: 0 },
      { type: "move-section", id: "a", x: 3, y: 0 },
    );
    expect(state.past).toHaveLength(base.past.length + 1);
    const undone = editorReducer(state, { type: "undo" });
    expect(sections(undone).find((entry) => entry.id === "a")?.layout.x).toBe(0);
  });

  it("starts a new undo step once a different section is moved", () => {
    const state = run(
      base,
      { type: "move-section", id: "a", x: 1, y: 0 },
      { type: "move-section", id: "b", x: 1, y: 3 },
    );
    expect(state.past).toHaveLength(base.past.length + 2);
  });
});

describe("removing, duplicating and page order", () => {
  const base = run(
    initEditor(store()),
    { type: "add-section", def: HERO, id: "a" },
    { type: "add-section", def: NOTE, id: "b" },
  );

  it("clears the selection when the selected section is removed", () => {
    const state = run(base, { type: "select", id: "a" }, { type: "remove-section", id: "a" });
    expect(sections(state).map((entry) => entry.id)).toEqual(["b"]);
    expect(state.selectedId).toBeNull();
    expect(selectedSection(state)).toBeUndefined();
  });

  it("puts a copy directly after the original, with its own settings object", () => {
    const state = run(base, { type: "duplicate-section", id: "a", newId: "a-copy" });
    expect(sections(state).map((entry) => entry.id)).toEqual(["a", "a-copy", "b"]);
    expect(sections(state)[1]?.settings).not.toBe(sections(state)[0]?.settings);
    expect(state.selectedId).toBe("a-copy");
  });

  it("moves a section through the page order and stops at the ends", () => {
    const forward = run(base, { type: "reorder-depth", id: "a", direction: "forward" });
    expect(sections(forward).map((entry) => entry.id)).toEqual(["b", "a"]);
    const past = run(base, { type: "reorder-depth", id: "a", direction: "backward" });
    expect(sections(past).map((entry) => entry.id)).toEqual(["a", "b"]);
  });
});

describe("settings, tokens and brand", () => {
  const base = run(initEditor(store()), { type: "add-section", def: HERO, id: "a" });

  it("writes a setting without disturbing the others", () => {
    const state = run(base, { type: "set-setting", id: "a", key: "heading", value: "New" });
    expect(sections(state)[0]?.settings).toEqual({ heading: "New", image: null });
  });

  it("keeps a deliberate null, which is how a merchant clears an image", () => {
    const state = run(base, { type: "set-setting", id: "a", key: "image", value: null });
    expect(state).toBe(base);
  });

  it("coalesces typing into one undo step, per section and per field", () => {
    const state = run(
      base,
      { type: "set-setting", id: "a", key: "heading", value: "N" },
      { type: "set-setting", id: "a", key: "heading", value: "Ne" },
      { type: "set-setting", id: "a", key: "heading", value: "New" },
    );
    expect(state.past).toHaveLength(base.past.length + 1);
    expect(sections(editorReducer(state, { type: "undo" }))[0]?.settings["heading"]).toBe("Hello");
  });

  it("edits a token and a brand field", () => {
    const state = run(
      base,
      { type: "set-token", key: "colorAccent", value: "#000000" },
      { type: "set-brand", key: "name", value: "Verma & Sons" },
    );
    expect(state.present.tokens.colorAccent).toBe("#000000");
    expect(state.present.brand.name).toBe("Verma & Sons");
  });
});

describe("undo, redo and the dirty flag", () => {
  it("walks back and forward through a sequence of edits", () => {
    let state = run(
      initEditor(store()),
      { type: "add-section", def: HERO, id: "a" },
      { type: "add-section", def: NOTE, id: "b" },
      { type: "set-setting", id: "a", key: "heading", value: "Changed" },
    );
    expect(sections(state)).toHaveLength(2);

    state = editorReducer(state, { type: "undo" });
    expect(sections(state)[0]?.settings["heading"]).toBe("Hello");
    state = editorReducer(state, { type: "undo" });
    expect(sections(state)).toHaveLength(1);
    state = editorReducer(state, { type: "undo" });
    expect(sections(state)).toHaveLength(0);
    expect(canUndo(state)).toBe(false);
    expect(isDirty(state)).toBe(false);

    state = editorReducer(state, { type: "redo" });
    state = editorReducer(state, { type: "redo" });
    state = editorReducer(state, { type: "redo" });
    expect(sections(state)).toHaveLength(2);
    expect(sections(state)[0]?.settings["heading"]).toBe("Changed");
    expect(canRedo(state)).toBe(false);
  });

  it("drops the redo branch once a new edit is made", () => {
    const state = run(
      initEditor(store()),
      { type: "add-section", def: HERO, id: "a" },
      { type: "undo" },
      { type: "add-section", def: NOTE, id: "b" },
    );
    expect(canRedo(state)).toBe(false);
    expect(sections(state).map((entry) => entry.id)).toEqual(["b"]);
  });

  it("is clean again when undo walks back to the last saved document", () => {
    const state = run(initEditor(store()), { type: "add-section", def: HERO, id: "a" });
    expect(isDirty(state)).toBe(true);
    expect(isDirty(editorReducer(state, { type: "undo" }))).toBe(false);
  });

  it("takes the document that was sent as the new baseline, not the one on screen", () => {
    const edited = run(initEditor(store()), { type: "add-section", def: HERO, id: "a" });
    const inFlight = edited.present;
    const afterMore = run(edited, { type: "add-section", def: NOTE, id: "b" });
    const saved = editorReducer(afterMore, { type: "saved", store: inFlight });
    expect(isDirty(saved)).toBe(true);
    expect(isDirty(editorReducer(saved, { type: "undo" }))).toBe(false);
  });

  it("makes discard itself undoable", () => {
    const state = run(
      initEditor(store()),
      { type: "add-section", def: HERO, id: "a" },
      { type: "discard" },
    );
    expect(sections(state)).toHaveLength(0);
    expect(isDirty(state)).toBe(false);
    expect(sections(editorReducer(state, { type: "undo" }))).toHaveLength(1);
  });
});

describe("pages and unknown sections", () => {
  it("switches page and forgets the selection, which belonged to the old one", () => {
    const state = run(
      initEditor(store()),
      { type: "add-section", def: HERO, id: "a" },
      { type: "set-page", pageId: "about" },
    );
    expect(state.selectedId).toBeNull();
    expect(currentPage(state)?.name).toBe("About");
  });

  it("moves a section of a type it has never heard of, and keeps its settings", () => {
    const withUnknown = initEditor({
      ...store(),
      pages: [
        {
          id: "home",
          route: "/",
          name: "Home",
          sections: [
            {
              id: "ghost",
              type: "from-a-newer-build",
              settings: { anything: "kept" },
              layout: { x: 0, y: 0, w: 6, h: 3 },
            },
          ],
        },
      ],
    });
    const state = run(withUnknown, { type: "move-section", id: "ghost", x: 3, y: 1 });
    expect(sections(state)[0]?.layout).toMatchObject({ x: 3, y: 1 });
    expect(sections(state)[0]?.settings).toEqual({ anything: "kept" });
  });
});
