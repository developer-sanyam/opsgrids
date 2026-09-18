import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MAX_ASSET_BYTES, UPLOAD_NEEDS_SERVICE } from "@/api/client";
import type { SettingField, SettingValue } from "@/theme/schema";

import { SettingControl } from "./SettingControl";

/**
 * The image control's states, with the real client behind it.
 *
 * Nothing about the upload is mocked here on purpose: the point of these is that a refusal happens
 * *in the browser*, before a byte is sent, so the `fetch` spy asserting "never called" is the
 * assertion and the message is the evidence of which check ran. A merchant who picked a 12 MB
 * camera original should be told in the same instant, not after a minute of upload.
 *
 * The resolver is given a base so the preview can be shown to go through it rather than to happen
 * to look right — see `theme/render/assetSrc.test.ts` for the resolver's own rules.
 */

const BASE = "https://cdn.example.test/opsgrids-public";
const KEY = "9a3f1c72-6b1e-4d0a-9a7f-2f5b8c1d4e60/3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331";
const PATH = `/a/${KEY}`;

vi.mock("@/theme/render/assetSrc", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/theme/render/assetSrc")>();
  return {
    ...actual,
    resolveImageSrc: (value: SettingValue) => actual.resolveImageSrc(value, BASE),
  };
});

const FIELD: SettingField = { key: "image", label: "Image", type: "image", default: null };

function Harness({ initial = null }: { initial?: SettingValue }) {
  const [value, setValue] = useState<SettingValue>(initial);
  return <SettingControl field={FIELD} value={value} onChange={setValue} />;
}

function fileOf(name: string, type: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

function pick(file: File): void {
  const input = screen.getByLabelText("Upload an image for Image");
  fireEvent.change(input, { target: { files: [file] } });
}

function urlBox(): HTMLInputElement {
  const box = screen.getByLabelText("Image");
  if (!(box instanceof HTMLInputElement)) throw new Error("the URL field is not a text input");
  return box;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the upload control", () => {
  it("offers only the formats the server accepts", () => {
    render(<Harness />);
    const input = screen.getByLabelText("Upload an image for Image");
    expect(input.getAttribute("accept")).toBe("image/jpeg,image/png,image/webp");
  });

  it("says what is accepted and that an upload is public, on the control itself", () => {
    render(<Harness />);
    const note = screen.getByText(/JPEG, PNG or WebP/);
    expect(note.textContent).toContain("SVG files aren't accepted");
    // ADR-0009 §1: this is a product requirement, not polish. A merchant who uploads a supplier
    // price list into an image slot has published it, and must not find out afterwards.
    expect(note.textContent).toContain("viewed by anyone who has its address");
  });

  it("is reachable and operable from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.tab();
    expect(document.activeElement).toBe(urlBox());
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Upload" }));
  });
});

describe("what it refuses before spending a request", () => {
  it("refuses an oversize file and names the limit", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<Harness />);

    pick(fileOf("huge.jpg", "image/jpeg", MAX_ASSET_BYTES + 1));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("over 5 MB");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses an SVG, and says so rather than letting the server say it", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<Harness />);

    pick(fileOf("logo.svg", "image/svg+xml", 600));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("SVG files can't be uploaded");
    expect(alert.textContent).toContain("PNG or WebP");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("leaves the setting exactly as it was when it refuses", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<Harness initial="https://images.merchant.example/hero.jpg" />);

    pick(fileOf("logo.svg", "image/svg+xml", 600));
    await screen.findByRole("alert");

    expect(urlBox().value).toBe("https://images.merchant.example/hero.jpg");
  });
});

describe("the error state", () => {
  it("is announced, and says what to do next", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<Harness />);

    // A file the browser is happy with: the refusal here is the honest one from fixtures mode.
    pick(fileOf("shopfront.jpg", "image/jpeg", 4096));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That image wasn't uploaded");
    expect(alert.textContent).toContain(UPLOAD_NEEDS_SERVICE);
  });

  it("recovers when dismissed", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn());
    render(<Harness />);

    pick(fileOf("logo.svg", "image/svg+xml", 600));
    await screen.findByRole("alert");

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("recovers when the merchant types an address instead", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn());
    render(<Harness />);

    pick(fileOf("logo.svg", "image/svg+xml", 600));
    await screen.findByRole("alert");

    await user.type(urlBox(), "https://images.merchant.example/hero.jpg");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(urlBox().value).toBe("https://images.merchant.example/hero.jpg");
  });

  it("lets the same file be picked again after a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<Harness />);
    const input = screen.getByLabelText("Upload an image for Image");

    pick(fileOf("logo.svg", "image/svg+xml", 600));
    await screen.findByRole("alert");

    // Cleared, so re-choosing the same file still fires `change` — otherwise a second attempt at
    // the same file is silent and the merchant thinks the button is broken.
    expect((input as HTMLInputElement).value).toBe("");
  });
});

describe("the preview", () => {
  it("shows the empty state, and says what happens instead", () => {
    render(<Harness />);
    expect(screen.getByText(/No image — the theme draws its own plate/)).toBeTruthy();
  });

  it("resolves an uploaded path through the resolver rather than using it raw", () => {
    const { container } = render(<Harness initial={PATH} />);
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe(`${BASE}/${KEY}`);
  });

  it("shows a pasted absolute address unchanged", () => {
    const { container } = render(<Harness initial="https://images.merchant.example/hero.jpg" />);
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe("https://images.merchant.example/hero.jpg");
  });

  it("falls back to words when the address does not load", () => {
    const { container } = render(<Harness initial="https://images.merchant.example/gone.jpg" />);
    const img = container.querySelector("img");
    if (img === null) throw new Error("no preview was rendered");

    fireEvent.error(img);

    expect(screen.getByText(/That image didn't load/)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("tries again when the address changes, instead of staying broken", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness initial="https://images.merchant.example/gone.jpg" />);
    const img = container.querySelector("img");
    if (img === null) throw new Error("no preview was rendered");

    fireEvent.error(img);
    expect(screen.getByText(/That image didn't load/)).toBeTruthy();

    await user.type(urlBox(), "2");

    expect(screen.queryByText(/That image didn't load/)).toBeNull();
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "https://images.merchant.example/gone.jpg2",
    );
  });
});
