import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, waitFor } from "storybook/test";

import { blankFile, dropFiles, productPhoto } from "../story-files";
import { Card } from "./Card";
import { FileUpload } from "./FileUpload";
import type { UploadControls } from "./FileUpload";

const IMAGES = ["image/png", "image/jpeg", "image/webp"];
const MB = 1024 * 1024;

type Upload = (file: File, controls: UploadControls) => Promise<string>;

let stored = 0;

/** A transport that answers at once, as a stored asset id would come back. */
const instant: Upload = async (file) => `asset-${file.name}-${++stored}`;

/** A transport caught mid-flight: 45% sent, and it never finishes — the state is the story. */
const inFlight: Upload = (_file, { onProgress }) => {
  onProgress(0.45);
  return new Promise<string>(() => undefined);
};

/** A transport whose server said no, in its own words. */
const refused: Upload = async () => {
  throw new Error("Storage is full for this organization. Remove unused images or ask an admin to raise the limit.");
};

function zone(canvasElement: HTMLElement): Element {
  const found = canvasElement.querySelector("button[aria-labelledby]");
  if (found === null) throw new Error("No drop zone.");
  return found;
}

const meta = {
  title: "Forms/FileUpload",
  component: FileUpload<string>,
  args: {
    label: "Product images",
    accept: IMAGES,
    maxSize: 5 * MB,
    maxFiles: 6,
    onUpload: instant,
  },
  render: (args) => (
    <Card className="max-w-xl p-4 sm:p-6">
      <FileUpload<string> {...args} />
    </Card>
  ),
} satisfies Meta<typeof FileUpload<string>>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing chosen yet: the prompt, and the rule generated from `accept`, `maxSize` and `maxFiles`. */
export const Empty: Story = {};

/** One file, and a second replaces it — the logo and avatar case. */
export const SingleFile: Story = { args: { label: "Company logo", maxFiles: 1, required: true } };

/** A pointer dragging files over the zone. */
export const Dragging: Story = {
  play: async ({ canvasElement }) => {
    const target = zone(canvasElement);
    target.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }));
    await waitFor(() => expect(target.hasAttribute("data-dragging")).toBe(true));
  },
};

export const Uploading: Story = {
  args: { onUpload: inFlight },
  play: async ({ canvas, canvasElement }) => {
    dropFiles(zone(canvasElement), [await productPhoto("canvas-laptop-bag.png")]);
    await canvas.findByRole("progressbar");
  },
};

export const Uploaded: Story = {
  play: async ({ canvas, canvasElement }) => {
    dropFiles(zone(canvasElement), [
      await productPhoto("canvas-laptop-bag.png", 0),
      await productPhoto("canvas-laptop-bag-side.png", 1),
      await productPhoto("canvas-laptop-bag-inside.png", 2),
    ]);
    await waitFor(() => {
      expect(canvas.getAllByText(/Uploaded$/)).toHaveLength(3);
    });
  },
};

/** The server refused it: its sentence on the row, with Retry and Remove. */
export const Failed: Story = {
  args: { onUpload: refused },
  play: async ({ canvas, canvasElement }) => {
    dropFiles(zone(canvasElement), [await productPhoto("canvas-laptop-bag.png")]);
    await canvas.findByRole("button", { name: "Retry" });
  },
};

/** Checked before sending: the wrong kind and too large are refused on the spot, and say why. */
export const Refused: Story = {
  play: async ({ canvas, canvasElement }) => {
    dropFiles(zone(canvasElement), [
      blankFile("animation.gif", "image/gif", 40 * 1024),
      blankFile("banner-original.png", "image/png", 7 * MB),
    ]);
    await canvas.findByRole("button", { name: "Dismiss banner-original.png" });
  },
};

/** An edit form's documents, stored before it opened. */
export const StoredFiles: Story = {
  args: {
    label: "Documents",
    optional: true,
    accept: [".pdf"],
    maxSize: 10 * MB,
    maxFiles: 3,
    initialFiles: [
      { name: "specifications.pdf", size: 250_880, value: "doc-1" },
      { name: "care-instructions.pdf", size: 98_304, value: "doc-2" },
    ],
  },
};

/** Every slot used: the zone says so and stops taking files until one is removed. */
export const Full: Story = {
  args: {
    label: "Documents",
    accept: [".pdf"],
    maxFiles: 2,
    initialFiles: [
      { name: "specifications.pdf", size: 250_880, value: "doc-1" },
      { name: "warranty-terms.pdf", size: 131_072, value: "doc-2" },
    ],
  },
};

export const FieldError: Story = {
  args: { required: true, error: "Add at least one product image before publishing." },
};

export const Disabled: Story = {
  args: {
    disabled: true,
    label: "Documents",
    accept: [".pdf"],
    initialFiles: [{ name: "specifications.pdf", size: 250_880, value: "doc-1" }],
  },
};

/** A long name truncates on its row; the whole name is still the Remove button's. */
export const LongFileName: Story = {
  args: {
    label: "Documents",
    accept: [".pdf"],
    initialFiles: [
      {
        name: "consolidated-northern-and-southern-cooperative-federation-supplier-price-list-2026-27-final-v3.pdf",
        size: 4_404_019,
        value: "doc-1",
      },
    ],
  },
};
