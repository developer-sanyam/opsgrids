import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { SectionDef, SectionInstance, SettingValue } from "@/theme/schema";

import { SettingsInspector } from "./SettingsInspector";

/**
 * The promise this file exists to keep: **adding a field to a `SectionDef` produces a working
 * control with no change to the editor.**
 *
 * So the def below is not one of the theme's. It is invented here, it uses every field type the
 * schema declares — including `color`, which no shipped section currently uses — and the editor
 * has never seen it. If the inspector ever grows a list of known sections or known keys, this
 * test is what fails.
 */

const INVENTED: SectionDef = {
  type: "something-new",
  name: "Something new",
  group: "commerce",
  defaultLayout: { w: 6, h: 4 },
  fields: [
    { key: "heading", label: "Heading", type: "text", default: "Hello" },
    { key: "body", label: "Body", type: "textarea", default: "Some copy" },
    { key: "count", label: "How many", type: "number", default: 3, help: "Between 1 and 9." },
    {
      key: "align",
      label: "Alignment",
      type: "select",
      options: [
        { value: "left", label: "Left" },
        { value: "center", label: "Centred" },
      ],
      default: "left",
    },
    { key: "framed", label: "Draw a frame", type: "toggle", default: false },
    { key: "tint", label: "Tint", type: "color", default: "#1a1a1a" },
    { key: "image", label: "Image", type: "image", default: null },
    {
      key: "source",
      label: "Products from",
      type: "product-source",
      options: [{ value: "all", label: "Whole catalogue" }],
      default: "all",
    },
  ],
};

/** `@testing-library/jest-dom` is not a dependency of these apps, so values are read off the
 *  control itself — the same way the other product suites assert. */
function valueOf(label: string): string {
  const element = screen.getByLabelText(label);
  if (
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement
  ) {
    return element.value;
  }
  throw new Error(`"${label}" is not a form control`);
}

function instance(settings: SectionInstance["settings"]): SectionInstance {
  return { id: "s1", type: INVENTED.type, settings, layout: { x: 0, y: 0, w: 6, h: 4 } };
}

/**
 * The inspector is controlled: its values come from the document, and an edit only shows once the
 * document has changed. This harness holds that document the way the shell's reducer does, so
 * typing behaves here the way it behaves in the app.
 */
function Harness({
  initial,
  def,
  onSettingChange,
}: {
  initial: SectionInstance;
  def: SectionDef | undefined;
  onSettingChange: (key: string, value: SettingValue) => void;
}) {
  const [section, setSection] = useState(initial);
  return (
    <SettingsInspector
      section={section}
      def={def}
      canEdit
      onSettingChange={(key, value) => {
        onSettingChange(key, value);
        setSection((current) => ({ ...current, settings: { ...current.settings, [key]: value } }));
      }}
      onMove={vi.fn()}
      onResize={vi.fn()}
      onDuplicate={vi.fn()}
      onRemove={vi.fn()}
      onDepth={vi.fn()}
    />
  );
}

function renderInspector(section: SectionInstance, def: SectionDef | undefined) {
  const onSettingChange = vi.fn();
  render(<Harness initial={section} def={def} onSettingChange={onSettingChange} />);
  return { onSettingChange };
}

const FULL = {
  heading: "Hello",
  body: "Some copy",
  count: 3,
  align: "left",
  framed: false,
  tint: "#1a1a1a",
  image: null,
  source: "all",
};

describe("controls generated from a SectionDef", () => {
  it("renders one labelled control per declared field, whatever the type", () => {
    renderInspector(instance(FULL), INVENTED);

    expect(valueOf("Heading")).toBe("Hello");
    expect(valueOf("Body")).toBe("Some copy");
    expect(valueOf("How many")).toBe("3");
    expect(valueOf("Alignment")).toBe("left");
    expect(
      screen.getByRole("switch", { name: "Draw a frame" }).getAttribute("aria-checked"),
    ).toBe("false");
    expect(valueOf("Tint")).toBe("#1a1a1a");
    expect(screen.getByLabelText("Tint — colour picker")).toBeDefined();
    expect(valueOf("Image")).toBe("");
    expect(valueOf("Products from")).toBe("all");
  });

  it("shows a field's help text", () => {
    renderInspector(instance(FULL), INVENTED);
    expect(screen.getByText("Between 1 and 9.")).toBeDefined();
  });

  it("reports a text edit as the field's key and its new value", async () => {
    const user = userEvent.setup();
    const { onSettingChange } = renderInspector(instance(FULL), INVENTED);

    await user.clear(screen.getByLabelText("Heading"));
    await user.type(screen.getByLabelText("Heading"), "Hi");

    expect(onSettingChange).toHaveBeenLastCalledWith("heading", "Hi");
  });

  it("reports a toggle as a boolean", async () => {
    const user = userEvent.setup();
    const { onSettingChange } = renderInspector(instance(FULL), INVENTED);

    await user.click(screen.getByRole("switch", { name: "Draw a frame" }));

    expect(onSettingChange).toHaveBeenCalledWith("framed", true);
  });

  it("reports a number as a number, and an emptied number field as null", async () => {
    const user = userEvent.setup();
    const { onSettingChange } = renderInspector(instance(FULL), INVENTED);

    await user.clear(screen.getByLabelText("How many"));
    expect(onSettingChange).toHaveBeenLastCalledWith("count", null);

    await user.type(screen.getByLabelText("How many"), "5");
    expect(onSettingChange).toHaveBeenLastCalledWith("count", 5);
  });

  it("refuses a colour that is not a six-digit hex, and says why", async () => {
    const user = userEvent.setup();
    const { onSettingChange } = renderInspector(instance(FULL), INVENTED);

    await user.clear(screen.getByLabelText("Tint"));
    await user.type(screen.getByLabelText("Tint"), "#12");

    expect(screen.getByText("Use a six-digit hex colour, like #1a1a1a.")).toBeDefined();
    expect(onSettingChange).not.toHaveBeenCalledWith("tint", "#12");
  });
});

describe("values that are not there", () => {
  it("renders a null into every control without crashing", () => {
    renderInspector(
      instance({
        heading: null,
        body: null,
        count: null,
        align: null,
        framed: null,
        tint: null,
        image: null,
        source: null,
      }),
      INVENTED,
    );

    expect(valueOf("Heading")).toBe("");
    expect(valueOf("How many")).toBe("");
    expect(valueOf("Alignment")).toBe("");
    expect(
      screen.getByRole("switch", { name: "Draw a frame" }).getAttribute("aria-checked"),
    ).toBe("false");
    expect(screen.getByText("No image — the theme draws its own plate.")).toBeDefined();
  });

  it("falls back to the declared default only when the key is absent entirely", () => {
    renderInspector(instance({}), INVENTED);
    expect(valueOf("Heading")).toBe("Hello");
  });

  it("keeps a stored option the theme no longer offers rather than silently changing it", () => {
    renderInspector(instance({ ...FULL, align: "justify" }), INVENTED);
    expect(valueOf("Alignment")).toBe("justify");
    expect(screen.getByText("justify (not in this theme)")).toBeDefined();
  });
});

describe("a section this build does not know", () => {
  it("is inspectable rather than fatal: it says so and shows what is stored", () => {
    renderInspector(
      { id: "s9", type: "from-a-newer-build", settings: { heading: "Kept" }, layout: { x: 0, y: 0, w: 6, h: 4 } },
      undefined,
    );

    expect(screen.getByText("This section isn't in the current theme")).toBeDefined();
    expect(screen.getByText("Kept")).toBeDefined();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDefined();
  });
});

describe("without permission to edit", () => {
  it("shows the settings as text and offers no controls at all", () => {
    render(
      <SettingsInspector
        section={instance(FULL)}
        def={INVENTED}
        canEdit={false}
        onSettingChange={vi.fn()}
        onMove={vi.fn()}
        onResize={vi.fn()}
        onDuplicate={vi.fn()}
        onRemove={vi.fn()}
        onDepth={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Heading")).toBeNull();
    expect(screen.getByText("Hello")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});
