import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Announcer } from "@opsgrids/ui";

import type { Brand, SectionDef, StoreDoc } from "@/theme/schema";

import { EditorShell } from "./EditorShell";

/**
 * The editing loop, driven the way a keyboard user drives it — which is also the only way a
 * pointer-driven canvas can be tested honestly in jsdom, where nothing has a size.
 *
 * The library here is invented rather than imported: the shell must work with whatever sections a
 * theme declares, and a suite that leans on the shipped ones stops noticing when it stops.
 */

const BLOCK: SectionDef = {
  type: "test-block",
  name: "Test block",
  group: "content",
  defaultLayout: { w: 6, h: 4 },
  fields: [{ key: "heading", label: "Heading", type: "text", default: "Hello" }],
};

const BANNER: SectionDef = {
  type: "test-banner",
  name: "Test banner",
  group: "layout",
  defaultLayout: { w: 12, h: 2 },
  fields: [],
};

const LIBRARY = [BLOCK, BANNER];

const BRAND: Brand = { name: "Verma Traders", tagline: "Since 1994", logoText: "VT" };

function store(): StoreDoc {
  return {
    themeKey: "depot",
    mode: "trade",
    tokens: {
      colorBg: "#ffffff",
      colorSurface: "#f7f7f7",
      colorText: "#121212",
      colorMuted: "#666666",
      colorAccent: "#1d4ed8",
      colorAccentText: "#ffffff",
      colorBorder: "#e3e3e3",
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

function renderShell(options: { canEdit?: boolean; onSave?: (doc: StoreDoc) => Promise<StoreDoc> } = {}) {
  const onSave = options.onSave ?? vi.fn(async (doc: StoreDoc) => doc);
  render(
    <MemoryRouter>
      <Announcer>
        <EditorShell
          store={store()}
          brand={BRAND}
          library={LIBRARY}
          products={[]}
          categories={[]}
          canEdit={options.canEdit ?? true}
          onSave={onSave}
        />
      </Announcer>
    </MemoryRouter>,
  );
  return { onSave };
}

/** The canvas frame for a section, found by the position its accessible name states. */
function frame(name: string): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(`^${name}, column`) });
}

describe("adding a section from the palette", () => {
  it("starts empty and says what to do about it", () => {
    renderShell();
    expect(screen.getByText("This page is empty")).toBeDefined();
  });

  it("adds one at the bottom, selects it, and announces where it landed", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Add Test block" }));

    expect(frame("Test block").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/Test block at column 1, row 1.*Added\./)).toBeDefined();
    expect(screen.queryByText("This page is empty")).toBeNull();
  });

  it("stacks the second one below the first rather than on top of it", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.click(screen.getByRole("button", { name: "Add Test banner" }));

    expect(frame("Test banner").textContent).toContain("row 5");
  });

  it("moves focus to the new section, so the keyboard can carry straight on", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Add Test block" }));

    await waitFor(() => expect(document.activeElement).toBe(frame("Test block")));
  });
});

describe("the keyboard path", () => {
  async function addOne() {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    frame("Test block").focus();
    return user;
  }

  it("moves a section one column at a time with the arrow keys", async () => {
    const user = await addOne();

    await user.keyboard("{ArrowRight}{ArrowRight}");

    expect(frame("Test block").textContent).toContain("column 3");
  });

  it("says where the section landed, politely, after each key", async () => {
    const user = await addOne();

    await user.keyboard("{ArrowRight}");

    expect(
      screen.getByText("Test block at column 2, row 1, 6 columns wide, 4 rows tall"),
    ).toBeDefined();
  });

  it("resizes with Shift and an arrow key", async () => {
    const user = await addOne();

    await user.keyboard("{Shift>}{ArrowDown}{/Shift}");

    expect(frame("Test block").textContent).toContain("5 rows tall");
  });

  it("will not resize below one column, and says the size it kept", async () => {
    const user = await addOne();

    await user.keyboard("{Shift>}{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}{/Shift}");

    expect(frame("Test block").textContent).toContain("1 of 12 columns wide");
  });

  it("will not move a section off the left edge", async () => {
    const user = await addOne();

    await user.keyboard("{ArrowLeft}{ArrowLeft}");

    expect(frame("Test block").textContent).toContain("column 1");
  });

  it("deletes with the Delete key and says undo is available", async () => {
    const user = await addOne();

    await user.keyboard("{Delete}");

    expect(screen.getByText("This page is empty")).toBeDefined();
    expect(screen.getByText("Test block deleted. Undo brings it back.")).toBeDefined();
  });

  it("reorders the page order from the list on the left", async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.click(screen.getByRole("button", { name: "Add Test banner" }));

    screen.getByRole("button", { name: "1 Test block" }).focus();
    await user.keyboard("{Alt>}{ArrowDown}{/Alt}");

    expect(screen.getByText("Test block is now 2 of 2 in the page order.")).toBeDefined();
  });
});

describe("undo and redo", () => {
  it("walks a sequence of edits back and forward again", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    frame("Test block").focus();
    await user.keyboard("{ArrowRight}");
    expect(frame("Test block").textContent).toContain("column 2");

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(frame("Test block").textContent).toContain("column 1");

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByText("This page is empty")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Redo" }));
    await user.click(screen.getByRole("button", { name: "Redo" }));
    expect(frame("Test block").textContent).toContain("column 2");
  });

  it("offers nothing to undo on a document nobody has touched", () => {
    renderShell();
    expect(screen.getByRole("button", { name: "Undo" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Redo" }).hasAttribute("disabled")).toBe(true);
  });
});

describe("saving", () => {
  it("is offered only once there is something to save, and reports when it is done", async () => {
    const user = userEvent.setup();
    const sent: StoreDoc[] = [];
    const onSave = vi.fn(async (doc: StoreDoc) => {
      sent.push(doc);
      return doc;
    });
    renderShell({ onSave });

    expect(screen.getByText("All changes saved")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save changes" }).hasAttribute("disabled")).toBe(true);

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    expect(screen.getByText("Unsaved changes")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.getByText("All changes saved")).toBeDefined());
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(sent[0]?.pages[0]?.sections).toHaveLength(1);
  });

  it("cannot be submitted twice by a double click", async () => {
    const user = userEvent.setup();
    const pending: { resolve: ((doc: StoreDoc) => void) | null } = { resolve: null };
    const onSave = vi.fn(
      () =>
        new Promise<StoreDoc>((done) => {
          pending.resolve = done;
        }),
    );
    renderShell({ onSave });

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("button", { name: "Saving…" }).hasAttribute("disabled")).toBe(true);

    await user.click(screen.getByRole("button", { name: "Saving…" }));
    expect(onSave).toHaveBeenCalledTimes(1);

    pending.resolve?.(store());
    await waitFor(() => expect(screen.getByText("All changes saved")).toBeDefined());
  });

  it("keeps the work on screen when the save fails, and offers another go", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(() => Promise.reject(new Error("nope")));
    renderShell({ onSave });

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(
        screen.getByText("Your changes couldn't be saved. They're still here — try again."),
      ).toBeDefined(),
    );
    expect(frame("Test block")).toBeDefined();
    expect(screen.getByText("Unsaved changes")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("discards back to the last saved document, and undo brings the work back", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.getByText("This page is empty")).toBeDefined();
    expect(screen.getByText("All changes saved")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(frame("Test block")).toBeDefined();
  });
});

describe("pages and preview width", () => {
  it("switches page and clears the selection with it", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.selectOptions(screen.getByLabelText("Page"), "about");

    expect(screen.getByText("This page is empty")).toBeDefined();
    expect(screen.getByText("Editing the About page.")).toBeDefined();
    expect(screen.getByText("Nothing selected")).toBeDefined();
  });

  it("says which width is being previewed", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Mobile" }));

    expect(screen.getByRole("button", { name: "Mobile" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Showing the phone width.")).toBeDefined();
  });
});

describe("without permission to edit", () => {
  it("shows the store read-only and offers no way to change it", () => {
    renderShell({ canEdit: false });

    expect(screen.getByText("You can't change this store's design")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Add Test block" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("opens the page manager to look, with nothing in it that changes the store", async () => {
    const user = userEvent.setup();
    renderShell({ canEdit: false });

    await user.click(screen.getByRole("button", { name: "Pages" }));

    const dialog = screen.getByRole("dialog", { name: "Pages" });
    // The row names itself in full: a screen reader gets the page, its address, what is on it and
    // where it sits, not four spans run together.
    expect(
      within(dialog).getByRole("button", { name: "Home — /, No sections yet, 1 of 2" }),
    ).toBeDefined();
    expect(within(dialog).queryByRole("button", { name: "Add page" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Delete About" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Rename About" })).toBeNull();
    // A UI gate hides a control; the server refuses the `PUT` either way.
    expect(within(dialog).queryByLabelText("Navigation for About")).toBeNull();
  });
});

/**
 * The page manager, driven entirely from the keyboard.
 *
 * A merchant with a 120-page sitemap lives in this dialog, and every act in it — add, rename,
 * re-route, reorder, place, delete — has to be reachable without a mouse, the same way the canvas
 * and the section list already are. Each one announces what the document became, not what was
 * asked for: an address is normalised on the way in, so announcing the typed string would be
 * announcing something that did not happen.
 */
describe("the page manager", () => {
  async function openManager(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Pages" }));
    return screen.getByRole("dialog", { name: "Pages" });
  }

  it("puts focus in the name field on open, so typing starts a page", async () => {
    const user = userEvent.setup();
    renderShell();

    await openManager(user);

    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("Page name")),
    );
  });

  it("adds a page by typing a name and pressing Enter, deriving the address from it", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.type(screen.getByLabelText("Page name"), "About Us{Enter}");

    expect(screen.getByText("About Us added at /about-us. You're editing it now.")).toBeDefined();
    // The form is ready for the next one, with focus back where typing continues.
    expect(screen.getByLabelText("Page name")).toHaveProperty("value", "");
    expect(document.activeElement).toBe(screen.getByLabelText("Page name"));
  });

  it("refuses a duplicate, keeps what was typed, and sends focus to the field that is wrong", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.type(screen.getByLabelText("Page name"), "About{Enter}");

    const address = screen.getByLabelText("Address");
    expect(document.activeElement).toBe(address);
    expect(address).toHaveProperty("value", "/about");
    expect(address.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText(/Another page already uses “\/about”/)).toBeDefined();
    // Nothing was added and nothing was lost.
    expect(screen.getByLabelText("Page name")).toHaveProperty("value", "About");
  });

  it("renames a page through its row, and says what it is called now", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.click(screen.getByRole("button", { name: "Rename About" }));
    const field = screen.getByLabelText("Page name for About");
    await user.clear(field);
    await user.type(field, "Who we are{Enter}");

    expect(screen.getByText("Page renamed to Who we are.")).toBeDefined();
    expect(screen.getByRole("button", { name: /^Who we are — \/about,/ })).toBeDefined();
  });

  it("will not let the home page move address, and says why on the field itself", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.click(screen.getByRole("button", { name: "Rename Home" }));

    const address = screen.getByLabelText("Address for Home");
    expect(address).toHaveProperty("disabled", true);
    expect(screen.getByText(/Every store needs a home page/)).toBeDefined();
    // Renaming it is still fine — the name is the merchant's, the address is not.
    expect(screen.getByLabelText("Page name for Home")).toHaveProperty("disabled", false);
  });

  it("re-routes a page the merchant owns, normalising what was typed", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.click(screen.getByRole("button", { name: "Rename About" }));
    const address = screen.getByLabelText("Address for About");
    await user.clear(address);
    await user.type(address, "Who We Are{Enter}");

    expect(screen.getByText("About now answers /who-we-are.")).toBeDefined();
  });

  it("moves a page through the order with Alt and an arrow key, and counts it out loud", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    const row = screen.getByRole("button", { name: "About — /about, No sections yet, 2 of 2" });
    row.focus();
    await user.keyboard("{Alt>}{ArrowUp}{/Alt}");

    expect(screen.getByText("About is now 1 of 2 in the navigation order.")).toBeDefined();
  });

  it("places a page in the footer from its row", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.selectOptions(screen.getByLabelText("Navigation for About"), "footer");

    expect(screen.getByText("About is in the footer.")).toBeDefined();
  });

  it("confirms a delete by naming the page and counting what goes with it", async () => {
    const user = userEvent.setup();
    renderShell();

    // Put work on the page first: "delete this page" is the same sentence for an empty page and
    // for an afternoon's work, and only the merchant knows which this is.
    await user.selectOptions(screen.getByLabelText("Page"), "about");
    await user.click(screen.getByRole("button", { name: "Add Test block" }));
    await user.click(screen.getByRole("button", { name: "Add Test banner" }));

    await openManager(user);
    await user.click(screen.getByRole("button", { name: "Delete About" }));

    expect(screen.getByText("Delete “About”?")).toBeDefined();
    expect(screen.getByText(/All 2 sections on it go too/)).toBeDefined();

    await user.keyboard("{Enter}");

    expect(screen.getByText("About deleted. Undo brings it back. Now editing Home.")).toBeDefined();
    expect(screen.queryByRole("button", { name: /About/ })).toBeNull();
  });

  it("backs out of a delete without closing the whole dialog", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    await user.click(screen.getByRole("button", { name: "Delete About" }));
    await user.keyboard("{Escape}");

    expect(screen.getByRole("dialog", { name: "Pages" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Delete About" })).toBeDefined();
  });

  it("offers no way to delete the pages a section links to implicitly", async () => {
    const user = userEvent.setup();
    renderShell();
    await openManager(user);

    expect(screen.queryByRole("button", { name: "Delete Home" })).toBeNull();
    expect(screen.getByRole("button", { name: "Delete About" })).toBeDefined();
  });
});
