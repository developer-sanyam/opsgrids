import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, fn, screen, waitFor } from "storybook/test";

import type { ImportExportViewActions } from "../lib/importExportView";
import {
  META,
  MENU_STATE,
  PREVIEW_MISSING_COLUMNS,
  PREVIEW_WITH_ERRORS,
  PROGRESS_RUNNING,
  PROGRESS_WAITING,
  SUMMARY_CANCELLED,
  SUMMARY_CLEAN,
  SUMMARY_WITH_PROBLEMS,
  XLSX_TYPE,
  stateFor,
} from "../story-import-export";
import { blankFile } from "../story-files";
import { ImportExportButton } from "./ImportExportButton";
import { PageHeader } from "./PageHeader";

const ACTIONS: ImportExportViewActions = {
  openFile: fn(async () => undefined),
  startImport: fn(async () => undefined),
  cancelImport: fn(),
  reset: fn(),
  downloadTemplate: fn(async () => undefined),
  exportAll: fn(async () => undefined),
  cancelExport: fn(),
  downloadReport: fn(async () => undefined),
};

/**
 * Every story renders the button in a page header and clicks it, so the dialog is open in the
 * phase the story's `state` names. The session is the caller's, so a story is a state and nothing
 * more: no timer moves it on while it is photographed.
 */
const meta = {
  title: "Overlays/ImportExportButton",
  component: ImportExportButton,
  tags: ["viewport"],
  args: { meta: META, state: MENU_STATE, actions: ACTIONS, canImport: true, canExport: true },
  render: (args) => <PageHeader eyebrow="Records" title="Companies" actions={<ImportExportButton {...args} />} />,
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "Import / Export" }));
    await screen.findByRole("dialog");
  },
} satisfies Meta<typeof ImportExportButton>;

export default meta;
type Story = StoryObj<typeof meta>;

/** What a list screen adds to its header: one secondary button, and nothing beside it. */
export const Closed: Story = {
  tags: ["!viewport"],
  play: async () => undefined,
};

/** The dialog as it opens: the three actions, the rules in plain words, focus on the first action. */
export const Menu: Story = {};

/** The sample sheet is being built: the row says so and ignores clicks, and keeps focus. */
export const MenuBusy: Story = { args: { state: stateFor({ downloadingTemplate: true }) } };

/** The export is paging through the list: progress in words and a bar, with a Cancel beside it. */
export const Exporting: Story = { args: { state: stateFor({ exporting: { fetched: 1400, total: 4800 } }) } };

/** The last download failed: its reason sits above the actions, in an alert. */
export const MenuWithNotice: Story = {
  args: { state: stateFor({ notice: "The export failed: the companies list could not be read. Try again." }) },
};

/** Someone who may read the list but not create records: no sample sheet, no Import. */
export const ExportOnly: Story = { args: { canImport: false } };

/** Someone who may create records but not read the list: no Export. */
export const ImportOnly: Story = { args: { canExport: false } };

/** A file dragged over the dialog: the Import row says where it will land. */
export const DraggingAFile: Story = {
  play: async (context) => {
    await meta.play(context);
    const transfer = new DataTransfer();
    transfer.items.add(blankFile("companies-september.xlsx", XLSX_TYPE, 2048));
    const target = await screen.findByRole("button", { name: "Import from Excel" });
    target.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer: transfer }));
    await waitFor(() => expect(target.hasAttribute("data-dragging")).toBe(true));
  },
};

/** The file is being read: nothing to do but wait, and the wait is said aloud once. */
export const Reading: Story = { args: { state: stateFor({ phase: "reading" }) } };

/** Rows to fix come first, each with its reason; sample rows are named and skipped; counts are words. */
export const PreviewWithErrors: Story = {
  args: { state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) },
};

/** A required column is missing: the reason is at the top and the import cannot start. */
export const PreviewMissingColumns: Story = {
  args: { state: stateFor({ phase: "preview", preview: PREVIEW_MISSING_COLUMNS }) },
};

/** Rows are going out: numbers, the counts so far, and the instruction to keep the tab open. */
export const Importing: Story = {
  args: { state: stateFor({ phase: "importing", progress: PROGRESS_RUNNING }) },
};

/** The server asked for a pause: the wait is in words, and the import carries on by itself. */
export const ImportingWaitingForRateLimit: Story = {
  args: { state: stateFor({ phase: "importing", progress: PROGRESS_WAITING }) },
};

/** Every row went in: no table of problems, and the sentence says everything imported. */
export const DoneClean: Story = {
  args: { state: stateFor({ phase: "done", summary: SUMMARY_CLEAN }) },
};

/** Rows that already existed, that the server refused and that were invalid, each with its reason. */
export const DoneWithFailures: Story = {
  args: { state: stateFor({ phase: "done", summary: SUMMARY_WITH_PROBLEMS }) },
};

/** Stopped by the user: what was made before it stopped, and that the rest was not sent. */
export const Cancelled: Story = {
  args: { state: stateFor({ phase: "done", summary: SUMMARY_CANCELLED }) },
};

/** The file could not be used: the engine's sentence in an alert, and Try again. */
export const CouldNotReadFile: Story = {
  args: {
    state: stateFor({
      phase: "error",
      error: "That file is 7 MB. The limit is 5 MB. Remove unused rows or split it into two files.",
    }),
  },
};
