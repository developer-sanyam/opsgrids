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
    mode: "trade",
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

/**
 * A bleeding section spans the whole canvas on the store — `PageRenderer` places it across the
 * rails and ignores its `x` and `w` entirely. Collision resolution settles overlaps *on* `x` and
 * `w`. Left disagreeing, the two sections below pass the editor's check as disjoint rectangles and
 * then paint on top of each other on the live store: measured in Chrome before the fix, the
 * bleeding band occupied 0–1280 and the block beside it 648–1236 on the same rows.
 *
 * The rule that settles it lives in this layer, not in `grid.ts`, because it is a fact about a
 * section's *settings* — and because toggling the setting is itself an edit that has to widen and
 * re-settle, which a collision resolver is never called for.
 */
describe("a bleeding section occupies all twelve columns", () => {
  const BAND: SectionDef = {
    type: "band",
    name: "Band",
    group: "content",
    defaultLayout: { w: 6, h: 3 },
    fields: [{ key: "bleed", label: "Full width", type: "toggle", default: false }],
  };

  /** Two half-width sections side by side on the same rows — legal, and how every case starts. */
  function sideBySide(): EditorState {
    return run(
      initEditor(store()),
      { type: "add-section", def: BAND, id: "left", at: { x: 0, y: 0 } },
      { type: "add-section", def: BAND, id: "right", at: { x: 6, y: 0 } },
    );
  }

  function byId(state: EditorState, id: string) {
    return sections(state).find((section) => section.id === id);
  }

  it("leaves two ordinary sections sharing rows exactly where they are", () => {
    const state = sideBySide();
    expect(byId(state, "left")?.layout).toMatchObject({ x: 0, y: 0, w: 6 });
    expect(byId(state, "right")?.layout).toMatchObject({ x: 6, y: 0, w: 6 });
  });

  it("collides with the section beside it the moment it starts bleeding", () => {
    const state = run(sideBySide(), {
      type: "set-setting",
      id: "left",
      key: "bleed",
      value: true,
    });
    expect(byId(state, "left")?.layout).toMatchObject({ x: 0, y: 0, w: 12, h: 3 });
    // Pushed clear, not left sitting on top of the band the way the store would paint it.
    expect(byId(state, "right")?.layout.y).toBe(3);
  });

  it("reads a document that stored the flag as a string, the way the page layer does", () => {
    const stored = initEditor({
      ...store(),
      pages: [
        {
          id: "home",
          route: "/",
          name: "Home",
          sections: [
            {
              id: "band",
              type: "band",
              settings: { bleed: "true" },
              layout: { x: 3, y: 0, w: 4, h: 3 },
            },
            { id: "block", type: "note", settings: {}, layout: { x: 8, y: 0, w: 4, h: 3 } },
          ],
        },
      ],
    });
    // Any edit on the page settles it: here the merchant simply nudges the block. The block is
    // the anchor, so it keeps its rectangle and the band — which now covers every column it sits
    // in — is the one pushed clear.
    const state = run(stored, { type: "move-section", id: "block", x: 8, y: 0 });
    expect(byId(state, "band")?.layout).toMatchObject({ x: 0, w: 12, y: 3 });
    expect(byId(state, "block")?.layout).toMatchObject({ x: 8, y: 0 });
  });

  it("cannot be dragged into a column, because there is no column it does not occupy", () => {
    const bleeding = run(sideBySide(), {
      type: "set-setting",
      id: "left",
      key: "bleed",
      value: true,
    });
    const state = run(bleeding, { type: "move-section", id: "left", x: 4, y: 0 });
    expect(byId(state, "left")?.layout).toMatchObject({ x: 0, w: 12 });
  });

  it("cannot be resized narrower than the canvas either", () => {
    const bleeding = run(sideBySide(), {
      type: "set-setting",
      id: "left",
      key: "bleed",
      value: true,
    });
    const state = run(bleeding, { type: "resize-section", id: "left", w: 4, h: 5 });
    // The height is the merchant's; the width is not theirs to give away while it bleeds.
    expect(byId(state, "left")?.layout).toMatchObject({ x: 0, w: 12, h: 5 });
  });

  it("gives the width back when the merchant turns bleeding off", () => {
    const state = run(
      sideBySide(),
      { type: "set-setting", id: "left", key: "bleed", value: true },
      { type: "set-setting", id: "left", key: "bleed", value: false },
      { type: "resize-section", id: "left", w: 4, h: 3 },
    );
    expect(byId(state, "left")?.layout).toMatchObject({ x: 0, w: 4 });
  });

  it("widens a copy of itself, and lands it clear of the original", () => {
    const bleeding = run(sideBySide(), {
      type: "set-setting",
      id: "left",
      key: "bleed",
      value: true,
    });
    const state = run(bleeding, { type: "duplicate-section", id: "left", newId: "copy" });
    expect(byId(state, "copy")?.layout).toMatchObject({ x: 0, w: 12, y: 3 });
  });

  it("stays undoable — turning it on is one step, and undo restores the narrow layout", () => {
    const before = sideBySide();
    const state = run(before, { type: "set-setting", id: "left", key: "bleed", value: true });
    const undone = run(state, { type: "undo" });
    expect(byId(undone, "left")?.layout).toMatchObject({ x: 0, w: 6 });
    expect(byId(undone, "right")?.layout.y).toBe(0);
    expect(undone.present).toBe(before.present);
  });
});

/**
 * Pages a merchant made, rather than pages a theme shipped.
 *
 * The ~120-page sitemap this product exists to serve could not start until a merchant could create
 * one page, and the renderer has resolved arbitrary routes since it was written — so the whole gap
 * was here. The address rules the five actions lean on are pinned in `pageRoutes.test.ts`; what is
 * pinned here is that the reducer actually applies them, that each action is one undo step, and
 * that the editor is never left pointing at a page that no longer exists.
 */
describe("adding, renaming, re-routing, reordering and deleting pages", () => {
  function routes(state: EditorState): string[] {
    return state.present.pages.map((page) => page.route);
  }

  it("adds an empty page at the merchant's address and opens it", () => {
    const state = run(initEditor(store()), {
      type: "add-page",
      id: "p1",
      route: "Contact Us",
      name: "Contact us",
    });
    const added = state.present.pages.at(-1);
    expect(added).toMatchObject({ id: "p1", route: "/contact-us", name: "Contact us" });
    expect(added?.sections).toEqual([]);
    // The next thing anyone does to a new page is put a section on it.
    expect(state.pageId).toBe("p1");
    expect(state.selectedId).toBeNull();
    expect(isDirty(state)).toBe(true);
  });

  it("names a page off its address when the merchant only gave one", () => {
    const state = run(initEditor(store()), { type: "add-page", id: "p1", route: "/shipping-info" });
    expect(state.present.pages.at(-1)?.name).toBe("Shipping info");
  });

  it("leaves a new page's navigation unsaid, which reads as the top navigation", () => {
    const state = run(initEditor(store()), { type: "add-page", id: "p1", route: "/contact" });
    expect(state.present.pages.at(-1)?.nav).toBeUndefined();
  });

  it("refuses a second page at an address the store already answers", () => {
    const before = initEditor(store());
    const state = run(before, { type: "add-page", id: "p1", route: "/About" });
    // Not an undo step, not a dirty document, not a silently broken store.
    expect(state).toBe(before);
    expect(canUndo(state)).toBe(false);
    expect(isDirty(state)).toBe(false);
  });

  it("refuses an address that belongs to the console", () => {
    const before = initEditor(store());
    expect(run(before, { type: "add-page", id: "p1", route: "/editor" })).toBe(before);
  });

  it("renames a page without touching its address", () => {
    const state = run(initEditor(store()), {
      type: "rename-page",
      pageId: "about",
      name: "  Who we are  ",
    });
    expect(state.present.pages[1]).toMatchObject({ route: "/about", name: "Who we are" });
  });

  it("refuses to rename a page to nothing — a blank nav item is not a page", () => {
    const before = initEditor(store());
    expect(run(before, { type: "rename-page", pageId: "about", name: "   " })).toBe(before);
  });

  it("coalesces a burst of typing in the name field into one undo step", () => {
    const state = run(
      initEditor(store()),
      { type: "rename-page", pageId: "about", name: "W" },
      { type: "rename-page", pageId: "about", name: "Wh" },
      { type: "rename-page", pageId: "about", name: "Who" },
    );
    expect(state.past).toHaveLength(1);
    expect(run(state, { type: "undo" }).present.pages[1]?.name).toBe("About");
  });

  it("re-routes a page the merchant made, normalising on the way in", () => {
    const state = run(initEditor(store()), {
      type: "set-page-route",
      pageId: "about",
      route: "Who We Are",
    });
    expect(routes(state)).toEqual(["/", "/who-we-are"]);
  });

  it("refuses to re-route a page onto another page's address", () => {
    const before = run(initEditor(store()), { type: "add-page", id: "p1", route: "/contact" });
    const state = run(before, { type: "set-page-route", pageId: "p1", route: "/about" });
    expect(state).toBe(before);
  });

  it("will not move the home page, delete it, or let anything else take its address", () => {
    const before = initEditor(store());
    expect(run(before, { type: "set-page-route", pageId: "home", route: "/start" })).toBe(before);
    expect(run(before, { type: "delete-page", pageId: "home" })).toBe(before);
    expect(run(before, { type: "set-page-route", pageId: "about", route: "/" })).toBe(before);
  });

  it("locks /catalogue and /product the same way, because sections link to them implicitly", () => {
    const withTheme = initEditor({
      ...store(),
      pages: [
        { id: "home", route: "/", name: "Home", sections: [] },
        { id: "cat", route: "/catalogue", name: "Catalogue", sections: [] },
        { id: "line", route: "/product", name: "Line detail", sections: [] },
      ],
    });
    expect(run(withTheme, { type: "delete-page", pageId: "cat" })).toBe(withTheme);
    expect(run(withTheme, { type: "delete-page", pageId: "line" })).toBe(withTheme);
    expect(run(withTheme, { type: "set-page-route", pageId: "cat", route: "/lines" })).toBe(
      withTheme,
    );
  });

  it("places a page in the navigation, and takes it out again", () => {
    const state = run(initEditor(store()), {
      type: "set-page-nav",
      pageId: "about",
      nav: "footer",
    });
    expect(state.present.pages[1]?.nav).toBe("footer");

    const hidden = run(state, { type: "set-page-nav", pageId: "about", nav: "none" });
    expect(hidden.present.pages[1]?.nav).toBe("none");

    // Setting a page to what it already is costs nothing — including the page that never said.
    expect(run(hidden, { type: "set-page-nav", pageId: "about", nav: "none" })).toBe(hidden);
    expect(run(state, { type: "set-page-nav", pageId: "home", nav: "primary" })).toBe(state);
  });

  it("moves a page through the order, which is the navigation order", () => {
    const state = run(
      initEditor(store()),
      { type: "add-page", id: "p1", route: "/contact" },
      { type: "reorder-pages", pageId: "p1", direction: "up" },
    );
    expect(routes(state)).toEqual(["/", "/contact", "/about"]);
  });

  it("does not fall off either end of the order", () => {
    const before = initEditor(store());
    expect(run(before, { type: "reorder-pages", pageId: "home", direction: "up" })).toBe(before);
    expect(run(before, { type: "reorder-pages", pageId: "about", direction: "down" })).toBe(before);
  });

  it("deletes a page with everything on it, in one undo step", () => {
    const withWork = run(
      initEditor(store()),
      { type: "set-page", pageId: "about" },
      { type: "add-section", def: HERO, id: "a" },
      { type: "add-section", def: NOTE, id: "b" },
    );
    const state = run(withWork, { type: "delete-page", pageId: "about" });
    expect(routes(state)).toEqual(["/"]);

    const undone = run(state, { type: "undo" });
    expect(undone.present.pages[1]?.sections.map((section) => section.id)).toEqual(["a", "b"]);
  });

  it("lands on the neighbour when the page being edited is the one deleted", () => {
    const three = run(
      initEditor(store()),
      { type: "add-page", id: "p1", route: "/contact" },
      { type: "set-page", pageId: "about" },
    );
    // The one after it…
    const forward = run(three, { type: "delete-page", pageId: "about" });
    expect(currentPage(forward)?.route).toBe("/contact");

    // …and the one before it, when there is nothing after.
    const last = run(three, { type: "set-page", pageId: "p1" });
    const back = run(last, { type: "delete-page", pageId: "p1" });
    expect(currentPage(back)?.route).toBe("/about");
    expect(back.selectedId).toBeNull();
  });

  it("leaves the editor where it is when some other page is deleted", () => {
    const state = run(
      initEditor(store()),
      { type: "add-page", id: "p1", route: "/contact" },
      { type: "set-page", pageId: "home" },
      { type: "delete-page", pageId: "p1" },
    );
    expect(state.pageId).toBe("home");
  });

  it("walks a whole page-editing session back and forward again", () => {
    const start = initEditor(store());
    const undo: EditorAction = { type: "undo" };
    const redo: EditorAction = { type: "redo" };
    const state = run(
      start,
      { type: "add-page", id: "p1", route: "/contact", name: "Contact" },
      { type: "set-page-nav", pageId: "p1", nav: "footer" },
      { type: "reorder-pages", pageId: "p1", direction: "up" },
      { type: "set-page-route", pageId: "p1", route: "/talk-to-us" },
      { type: "delete-page", pageId: "about" },
    );
    expect(routes(state)).toEqual(["/", "/talk-to-us"]);

    const back = run(state, undo, undo, undo, undo, undo);
    expect(back.present).toBe(start.present);
    expect(isDirty(back)).toBe(false);
    expect(canUndo(back)).toBe(false);

    const forward = run(back, redo, redo, redo, redo, redo);
    expect(routes(forward)).toEqual(["/", "/talk-to-us"]);
    expect(forward.present.pages[1]?.nav).toBe("footer");
    expect(isDirty(forward)).toBe(true);
  });
});
