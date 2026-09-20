/**
 * The content seven: the blocks that make the sitemap's company, support and legal pages
 * composable.
 *
 * What is worth pinning in jsdom is the same as everywhere else in this folder — the *document*
 * each section produces (which element, which rank, which name) and the strings this code computes
 * (a column count, a font-size, an iframe's `src`). Whether a gallery's captions collide and
 * whether a Terms page's measure is comfortable are browser questions and were checked there.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { defaultSettings } from "../sections";
import type { SectionInstance, SettingValue } from "../schema";
import { PageRenderer } from "./PageRenderer";
import { SectionRenderer } from "./SectionRenderer";

const PHONE = 390;
const DESKTOP = 1280;

function renderSection(
  type: string,
  settings: Record<string, SettingValue> = {},
  width: number = DESKTOP,
) {
  const section: SectionInstance = {
    id: `test-${type}`,
    type,
    settings: { ...defaultSettings(type), ...settings },
    layout: { x: 0, y: 0, w: 12, h: 8 },
  };
  return render(
    <MemoryRouter>
      <SectionRenderer
        section={section}
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        stacked={width < 768}
        width={width}
        mode="trade"
      />
    </MemoryRouter>,
  );
}

/** Every numbered slot of a section, cleared — the state a merchant reaches by emptying the demo copy. */
function cleared(type: string): Record<string, SettingValue> {
  return Object.fromEntries(
    Object.entries(defaultSettings(type)).map(([key, value]) => [
      key,
      /^item\d+/.test(key) ? (typeof value === "string" ? "" : null) : value,
    ]),
  );
}

/* ------------------------------- page-header ------------------------------ */

describe("the interior page header", () => {
  it("carries the page's own h1 by default, because an interior page's title is its h1", () => {
    renderSection("page-header", { heading: "Shipping policy" });
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Shipping policy");
  });

  it("steps down to an h2 when the merchant says the page already has a hero", () => {
    renderSection("page-header", { heading: "Shipping policy", rank: "section" });
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Shipping policy");
  });

  it("sets both ranks at the same size, so switching rank is an outline change and not a redesign", () => {
    const asPage = renderSection("page-header", { heading: "About us" }, DESKTOP);
    const pageSize = asPage.container.querySelector("h1")?.style.fontSize;
    asPage.unmount();

    const asSection = renderSection("page-header", { heading: "About us", rank: "section" }, DESKTOP);
    expect(asSection.container.querySelector("h2")?.style.fontSize).toBe(pageSize);
  });

  it("sets the title at the shared xl step, which is what stops the copied ramp drifting", () => {
    /* `StoreHeading` types `level` as 2 | 3 | 4, so this section renders its own heading element
       and repeats the `xl` numbers. This is the guard: `rich-text` at `xl` goes through the
       primitive, and the two have to land on the same declaration at the same canvas width. */
    const prose = renderSection("rich-text", { size: "xl", heading: "Same step" }, DESKTOP);
    const fromPrimitive = prose.container.querySelector("h2")?.style.fontSize;
    prose.unmount();

    const header = renderSection("page-header", { heading: "Same step" }, DESKTOP);
    expect(header.container.querySelector("h1")?.style.fontSize).toBe(fromPrimitive);
    expect(fromPrimitive).not.toContain("vw");
  });

  it("says what is missing instead of rendering an empty title", () => {
    renderSection("page-header", { heading: "", standfirst: "" });
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByText("No page title yet")).not.toBeNull();
  });

  it("takes a ground, and a merchant's tint in front of it", () => {
    const grounded = renderSection("page-header", { tone: "accent" });
    const section = grounded.container.querySelector<HTMLElement>("section");
    expect(section?.style.background).toBe("var(--st-accent)");
    expect(section?.style.color).toBe("var(--st-accent-text)");
    grounded.unmount();

    const tinted = renderSection("page-header", { tone: "accent", tint: "#f6d155" });
    const painted = tinted.container.querySelector<HTMLElement>("section");
    expect(painted?.style.background).toBe("rgb(246, 209, 85)");
    // A tint is a brand *surface*, so the words on it stay the theme's own text.
    expect(painted?.style.color).toBe("var(--st-text)");
  });

  it("insets a full-width header's words to the page's own content column", () => {
    const { container } = renderSection("page-header", { bleed: true });
    const inline = container.querySelector<HTMLElement>("section")?.style.paddingInline ?? "";
    expect(inline).toContain("max(");
    expect(inline).toContain("--st-max-width");
  });

  it("gives a composed interior page exactly one h1 — the assertion the library sweep makes", () => {
    render(
      <MemoryRouter>
        <PageRenderer
          page={{
            id: "about",
            route: "/about",
            name: "About",
            sections: [
              {
                id: "a",
                type: "page-header",
                settings: { ...defaultSettings("page-header"), heading: "About us" },
                layout: { x: 0, y: 0, w: 12, h: 5 },
              },
              {
                id: "b",
                type: "long-form",
                settings: defaultSettings("long-form"),
                layout: { x: 0, y: 5, w: 8, h: 18 },
              },
              {
                id: "c",
                type: "team-grid",
                settings: defaultSettings("team-grid"),
                layout: { x: 0, y: 23, w: 12, h: 10 },
              },
            ],
          }}
          products={PRODUCTS}
          categories={CATEGORY_LIST}
          width={DESKTOP}
          mode="trade"
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});

/* -------------------------------- long-form ------------------------------- */

const POLICY = [
  "We keep this short.",
  "",
  "## What we collect",
  "",
  "- Your name",
  "- Your address",
  "",
  "## Your rights",
  "",
  "1. Ask what we hold",
  "2. Ask us to remove it",
].join("\n");

describe("long-form prose", () => {
  it("renders real headings, paragraphs and lists — no markup from the merchant's text", () => {
    const { container } = renderSection("long-form", { body: POLICY, heading: "" });

    expect(container.querySelectorAll("h2")).toHaveLength(2);
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(screen.getByText("We keep this short.").tagName).toBe("P");
  });

  it("steps its subheadings down when the section carries a heading of its own", () => {
    const { container } = renderSection("long-form", { body: POLICY, heading: "Privacy" });
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(container.querySelectorAll("h3")).toHaveLength(2);
  });

  it("hides the table of contents until it is asked for", () => {
    const { container } = renderSection("long-form", { body: POLICY });
    expect(container.querySelector("nav")).toBeNull();
  });

  it("gives the table of contents an anchor for every subheading, and every anchor a target", () => {
    const { container } = renderSection("long-form", {
      body: POLICY,
      heading: "",
      showToc: true,
      tocLabel: "On this page",
    });

    const nav = screen.getByRole("navigation", { name: "On this page" });
    const hrefs = [...within(nav).getAllByRole("link")].map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["#what-we-collect", "#your-rights"]);

    const ids = [...container.querySelectorAll("h2")].map((heading) => heading.id);
    expect(ids).toEqual(["what-we-collect", "your-rights"]);
    for (const href of hrefs) {
      expect(container.querySelector(`${href}`)).not.toBeNull();
    }
  });

  it("holds the measure to a sane number of characters rather than the whole column", () => {
    // 50ch of a zero is about seventy characters of prose — measured in Chrome, because the ratio
    // between `ch` and an average letter is exactly the sort of thing jsdom cannot tell you.
    const comfortable = renderSection("long-form", { body: POLICY });
    expect(
      comfortable.container.querySelector<HTMLElement>("section > div")?.style.maxWidth,
    ).toBe("50ch");
    comfortable.unmount();

    const full = renderSection("long-form", { body: POLICY, measure: "full" });
    expect(full.container.querySelector<HTMLElement>("section > div")?.style.maxWidth).toBe("none");
  });

  it("says what to write when the body is empty", () => {
    renderSection("long-form", { body: "", heading: "" });
    expect(screen.getByText("No text yet")).not.toBeNull();
  });

  it("gives the contents no marker of its own, so a numbered clause is not numbered twice", () => {
    const { container } = renderSection("long-form", { body: POLICY, showToc: true });
    const list = container.querySelector<HTMLElement>("nav ol");
    // Still an `<ol>` — the order is information — but "7. 7. Governing law" is not.
    expect(list).not.toBeNull();
    expect(list?.style.listStyle).toBe("none");
  });
});

/* ------------------------- the pack's own box rule ------------------------ */

describe("no section in this pack sizes itself against its grid area", () => {
  /*
   * A percentage height against a grid area whose height is still being decided contributes
   * nothing to the row sizing, so a block whose content runs past the rows it was dropped into
   * keeps its short box and overflows it — and a section that paints a ground then paints over the
   * section below it. Found in Chrome on a composed About page; invisible in jsdom, where there is
   * no layout at all. What *is* checkable here is the declaration, which is the cause.
   */
  it.each([
    "page-header",
    "long-form",
    "media-gallery",
    "team-grid",
    "card-deck",
    "credential-strip",
    "video",
  ])("%s declares no percentage height on its root", (type) => {
    const { container } = renderSection(type);
    const root = container.querySelector<HTMLElement>("section");
    expect(root?.style.height, `${type} sets height: ${root?.style.height}`).toBe("");
    expect(root?.style.minHeight ?? "").not.toContain("%");
  });
});

/* ------------------------------ media-gallery ----------------------------- */

describe("the picture gallery", () => {
  it("skips an empty slot instead of drawing a hole", () => {
    const { container } = renderSection("media-gallery", {
      ...cleared("media-gallery"),
      item1Caption: "One",
      item3Caption: "Three",
    });
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelectorAll("figcaption")).toHaveLength(2);
  });

  it("carries all twelve slots, which is why the cap is twelve and not eight", () => {
    const filled = Object.fromEntries(
      Array.from({ length: 12 }, (_, index) => [`item${index + 1}Caption`, `Picture ${index + 1}`]),
    );
    const { container } = renderSection("media-gallery", filled);
    expect(container.querySelectorAll("li")).toHaveLength(12);
  });

  it("takes its column count from mobileColumns on a phone and columns above it", () => {
    const settings = { columns: 4, mobileColumns: 3 };

    const phone = renderSection("media-gallery", settings, PHONE);
    expect(phone.container.querySelector<HTMLElement>("ul")?.style.gridTemplateColumns).toBe(
      "repeat(3, minmax(0, 1fr))",
    );
    phone.unmount();

    const desktop = renderSection("media-gallery", settings, DESKTOP);
    expect(desktop.container.querySelector<HTMLElement>("ul")?.style.gridTemplateColumns).toBe(
      "repeat(4, minmax(0, 1fr))",
    );
  });

  it("leaves a captioned picture's alt empty, so the caption is not read out twice", () => {
    const { container } = renderSection("media-gallery", {
      ...cleared("media-gallery"),
      item1Image: "/img/works.jpg",
      item1Caption: "The press shop",
    });
    expect(container.querySelector("img")?.getAttribute("alt")).toBe("");
    expect(screen.getByText("The press shop").tagName).toBe("FIGCAPTION");
  });

  it("says so when there is nothing in it", () => {
    renderSection("media-gallery", cleared("media-gallery"));
    expect(screen.getByText("No pictures yet")).not.toBeNull();
  });
});

/* -------------------------------- team-grid ------------------------------- */

describe("the team grid", () => {
  it("shows a person once they have a name, and not before", () => {
    const { container } = renderSection("team-grid", {
      ...cleared("team-grid"),
      item1Name: "Anil Deshmukh",
      item1Role: "Managing Director",
      // A photograph with nobody attached to it is not a person.
      item2Image: "/img/someone.jpg",
    });
    expect(container.querySelectorAll("li")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Anil Deshmukh");
    expect(screen.getByText("Managing Director")).not.toBeNull();
  });

  it("rounds the portrait when asked, without changing what is in the document", () => {
    const { container } = renderSection("team-grid", { shape: "circle" });
    const plate = container.querySelector<HTMLElement>("li img, li svg");
    expect(plate?.style.borderRadius).toBe("50%");
    expect(container.querySelectorAll("li").length).toBeGreaterThan(0);
  });

  it("says so when nobody has been added", () => {
    renderSection("team-grid", cleared("team-grid"));
    expect(screen.getByText("Nobody here yet")).not.toBeNull();
  });
});

/* -------------------------------- card-deck ------------------------------- */

describe("the card deck", () => {
  it("makes the whole card one link, whose name carries the title and the summary", () => {
    renderSection("card-deck", {
      ...cleared("card-deck"),
      item1Title: "Emergency shutdown supply",
      item1Meta: "Thermax · 2024",
      item1Body: "On site before the next shift.",
      item1Href: "/store/contact",
      linkLabel: "Read more",
    });

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]?.getAttribute("href")).toBe("/store/contact");
    expect(links[0]?.textContent).toContain("Emergency shutdown supply");
    expect(links[0]?.textContent).toContain("Read more");
  });

  it("is not a control at all when the merchant left the link empty", () => {
    const { container } = renderSection("card-deck", {
      ...cleared("card-deck"),
      item1Title: "A role we have not opened yet",
      linkLabel: "Read more",
    });
    expect(screen.queryByRole("link")).toBeNull();
    // ...and the link wording is not shown on a card that does not link anywhere.
    expect(container.textContent).not.toContain("Read more");
  });

  it("drops the picture entirely for a deck of roles, which is what a Careers page is", () => {
    const withPicture = renderSection("card-deck", { aspect: "wide" });
    expect(withPicture.container.querySelectorAll("li img, li svg").length).toBeGreaterThan(0);
    withPicture.unmount();

    const roles = renderSection("card-deck", { aspect: "none" });
    expect(roles.container.querySelectorAll("li img, li svg")).toHaveLength(0);
    expect(roles.container.querySelectorAll("li").length).toBeGreaterThan(0);
  });

  it("skips a card with no title and says so when every card is empty", () => {
    renderSection("card-deck", cleared("card-deck"));
    expect(screen.getByText("No cards yet")).not.toBeNull();
  });
});

/* ---------------------------- credential-strip ---------------------------- */

describe("the credential strip", () => {
  it("sets the short code as a wordmark when the merchant has no artwork", () => {
    const { container } = renderSection("credential-strip", {
      ...cleared("credential-strip"),
      item1Mark: "ISO 9001",
      item1Label: "Quality management system",
      item1Issuer: "TÜV SÜD",
    });

    expect(container.querySelectorAll("li")).toHaveLength(1);
    expect(container.querySelector("img")).toBeNull();
    // The code is in the document as a heading, not only as the decorative mark.
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("ISO 9001");
    expect(screen.getByText("TÜV SÜD")).not.toBeNull();
  });

  it("uses the merchant's own mark when there is one, and does not announce it twice", () => {
    const { container } = renderSection("credential-strip", {
      ...cleared("credential-strip"),
      item1Mark: "ISO 14001",
      item1Image: "/img/iso-14001.png",
    });
    expect(container.querySelector("img")?.getAttribute("alt")).toBe("");
  });

  it("says so when there is nothing to show", () => {
    renderSection("credential-strip", cleared("credential-strip"));
    expect(screen.getByText("No certifications yet")).not.toBeNull();
  });
});

/* ---------------------------------- video --------------------------------- */

describe("the video block's click-to-play facade", () => {
  const FILM = {
    item1Title: "A day on the line",
    item1Meta: "4 min · Factory tour",
    item1Embed: "https://www.youtube-nocookie.com/embed/abc123",
    item1Href: "https://www.youtube.com/watch?v=abc123",
  };

  it("loads nothing third-party until someone presses play", () => {
    const { container } = renderSection("video", { ...cleared("video"), ...FILM });

    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.getByRole("button", { name: "Play, A day on the line" })).not.toBeNull();
    // The visitor is told what pressing it does, before they press it.
    expect(
      screen.getByText("Pressing play loads the film from the host, which may set its own cookies."),
    ).not.toBeNull();
  });

  it("creates the frame on the press, from the address the merchant pasted", async () => {
    const user = userEvent.setup();
    const { container } = renderSection("video", { ...cleared("video"), ...FILM });

    await user.click(screen.getByRole("button", { name: "Play, A day on the line" }));

    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("src")).toBe(
      "https://www.youtube-nocookie.com/embed/abc123?autoplay=1",
    );
    expect(frame?.getAttribute("title")).toBe("A day on the line");
    // Named, and focused, because the control that was in the tab order has just been replaced.
    expect(document.activeElement).toBe(frame);
  });

  it("refuses an address that is not https, rather than putting it in an src", () => {
    for (const embed of [
      "javascript:alert(1)",
      "http://insecure.example/embed/1",
      "//protocol-relative.example/embed/1",
      "not a url at all",
    ]) {
      const view = renderSection("video", { ...cleared("video"), ...FILM, item1Embed: embed });
      expect(view.container.querySelector("iframe"), embed).toBeNull();
      expect(view.queryByRole("button", { name: /^Play/ }), embed).toBeNull();
      view.unmount();
    }
  });

  it("falls back to a named way out when there is no embed address", () => {
    renderSection("video", {
      ...cleared("video"),
      ...FILM,
      item1Embed: "",
      watchLabel: "Watch on the host's site",
    });

    expect(screen.queryByRole("button", { name: /^Play/ })).toBeNull();
    // Four films would otherwise be four links all called the same thing.
    expect(
      screen.getByRole("link", { name: "Watch on the host's site, A day on the line" }),
    ).not.toBeNull();
    // And with no embed there is nothing to warn about.
    expect(screen.queryByText(/may set its own cookies/)).toBeNull();
  });

  it("plays one film at a time", async () => {
    const user = userEvent.setup();
    const { container } = renderSection("video", {
      ...cleared("video"),
      ...FILM,
      item2Title: "How we test every batch",
      item2Embed: "https://player.vimeo.com/video/999",
    });

    await user.click(screen.getByRole("button", { name: "Play, A day on the line" }));
    await user.click(screen.getByRole("button", { name: "Play, How we test every batch" }));

    const frames = container.querySelectorAll("iframe");
    expect(frames).toHaveLength(1);
    expect(frames[0]?.getAttribute("src")).toContain("player.vimeo.com/video/999");
  });

  it("holds a film to one column on a phone whatever the column setting says", () => {
    const phone = renderSection("video", { columns: 3 }, PHONE);
    expect(phone.container.querySelector<HTMLElement>("ul")?.style.gridTemplateColumns).toBe(
      "repeat(1, minmax(0, 1fr))",
    );
  });

  it("says so when no film has been added", () => {
    renderSection("video", cleared("video"));
    expect(screen.getByText("No videos yet")).not.toBeNull();
  });
});
