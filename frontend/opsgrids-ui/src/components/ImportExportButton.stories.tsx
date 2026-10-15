import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import type { ReactNode } from "react";
import { Link, RouterProvider, createMemoryRouter } from "react-router-dom";
import { expect, fn, screen, waitFor } from "storybook/test";

import type { ImportExportViewActions, ImportExportViewState } from "../lib/importExportView";
import {
  META,
  MENU_STATE,
  PREVIEW_MISSING_COLUMNS,
  PREVIEW_UPDATE_ALL_UNCHANGED,
  PREVIEW_UPDATE_CANNOT_MATCH,
  PREVIEW_UPDATE_MIXED,
  PREVIEW_UPDATE_NO_BASELINE,
  PREVIEW_UPDATE_REFUSALS,
  PREVIEW_UPDATE_ROWS_WITHOUT_ID,
  PREVIEW_UPDATE_UNUSABLE_BASELINE,
  PREVIEW_WITH_ERRORS,
  PREVIEW_WITH_EXISTING,
  PROGRESS_RUNNING,
  PROGRESS_UPDATING,
  PROGRESS_WAITING,
  SUMMARY_CANCELLED,
  SUMMARY_CLEAN,
  SUMMARY_UPDATE_PARTLY_SAVED,
  SUMMARY_UPDATE_WITH_FAILURES,
  SUMMARY_WITH_PROBLEMS,
  UPDATE_META,
  XLSX_TYPE,
  stateFor,
} from "../story-import-export";
import { blankFile } from "../story-files";
import { ImportExportButton } from "./ImportExportButton";
import type { ImportExportButtonProps } from "./ImportExportButton";
import { PageHeader } from "./PageHeader";

const ACTIONS: ImportExportViewActions = {
  setMode: fn(async () => undefined),
  openFile: fn(async () => undefined),
  startImport: fn(async () => undefined),
  cancelImport: fn(),
  reset: fn(),
  downloadTemplate: fn(async () => undefined),
  exportAll: fn(async () => undefined),
  cancelExport: fn(),
  downloadReport: fn(async () => undefined),
};

/** A data router, as every app builds one: the leave question needs it, and a story without one has none. */
function InDataRouter({ children }: { children: ReactNode }) {
  const [router] = useState(() =>
    createMemoryRouter([
      { path: "/", element: children },
      { path: "/deals", element: <h1>Deals</h1> },
    ]),
  );
  return <RouterProvider router={router} />;
}

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

/**
 * A list that can be updated from a sheet: the choice sits between the sample sheet and Import, the
 * action it changes, with adding only chosen. Nothing about updating is on screen until it is chosen.
 */
export const MenuWithModeChoice: Story = { args: { meta: UPDATE_META } };

/** Updating chosen: its rules open under the choice, in plain words, and Import says it adds and updates. */
export const MenuUpdateChosen: Story = {
  args: { meta: UPDATE_META, state: stateFor({ mode: "addAndUpdate" }) },
};

/** The file is being read: nothing to do but wait, and the wait is said aloud once. */
export const Reading: Story = { args: { state: stateFor({ phase: "reading" }) } };

/** Rows to fix come first, each with its reason; sample rows are named and skipped; counts are words. */
export const PreviewWithErrors: Story = {
  args: { state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) },
};

/** A row already on file is "Already exists", apart from the one that needs fixing. */
export const PreviewWithExistingRows: Story = {
  args: { state: stateFor({ phase: "preview", preview: PREVIEW_WITH_EXISTING }) },
};

/** A required column is missing: the reason is at the top and the import cannot start. */
export const PreviewMissingColumns: Story = {
  args: { state: stateFor({ phase: "preview", preview: PREVIEW_MISSING_COLUMNS }) },
};

/** Update mode also loads every stored record to compare the rows with, and says so while it waits. */
export const ReadingRecords: Story = {
  args: { meta: UPDATE_META, state: stateFor({ phase: "reading", mode: "addAndUpdate" }) },
};

/**
 * Update mode on a file: each row says what it will do (New, Update with the columns it changes, No
 * change), the counts are words, and the choice stays in reach to switch back and check the file again.
 */
export const PreviewUpdateMixed: Story = {
  args: { meta: UPDATE_META, state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_MIXED }) },
};

/** Every row already matches its record: nothing to send, and the button says so and cannot be pressed. */
export const PreviewUpdateAllUnchanged: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_ALL_UNCHANGED }),
  },
};

/** Rows refused before anything is sent: an unknown Record ID, an immutable cell that differs, and two more, each with its reason. */
export const PreviewUpdateRefusals: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_REFUSALS }),
  },
};

/** Update mode on a file with no id or key column: it cannot match anything, and says so above the rows. */
export const PreviewUpdateCannotMatch: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_CANNOT_MATCH }),
  },
};

/**
 * A file with no export baseline: the warning says every filled cell is compared with the record as
 * it is now, and that an old cell puts its old value back. One row is found by a name in other
 * capitals and carries the note that says why nothing will be sent for it.
 */
export const PreviewUpdateNoBaseline: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_NO_BASELINE }),
  },
};

/** A baseline that is damaged or another list's is ignored, and the preview says so before the same warning. */
export const PreviewUpdateUnusableBaseline: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_UNUSABLE_BASELINE }),
  },
};

/** A list matched by its id alone: the rows that have none are counted, above the rows, as new records. */
export const PreviewUpdateRowsWithoutId: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "preview", mode: "addAndUpdate", preview: PREVIEW_UPDATE_ROWS_WITHOUT_ID }),
  },
};

/** Import found the records had changed while the preview was open: it shows the new one instead of sending anything. */
export const PreviewUpdateRefreshed: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({
      phase: "preview",
      mode: "addAndUpdate",
      preview: PREVIEW_UPDATE_MIXED,
      notice:
        "The records changed while you were reviewing this file, so the preview has been refreshed and nothing was imported. Check it, then choose Import again.",
    }),
  },
};

/**
 * A state that follows the choice, as the hook does: the file is first previewed for adding, and
 * choosing to update on the preview itself shows its rules open under the choice, not behind a heading.
 */
function SwitchingOnThePreview(args: ImportExportButtonProps) {
  const [state, setState] = useState<ImportExportViewState>(
    stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }),
  );
  const actions: ImportExportViewActions = {
    ...args.actions,
    setMode: async (mode) => {
      setState(
        mode === "addAndUpdate"
          ? stateFor({ phase: "preview", mode, preview: PREVIEW_UPDATE_MIXED })
          : stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }),
      );
    },
  };
  return (
    <PageHeader
      eyebrow="Records"
      title="Items"
      actions={<ImportExportButton {...args} state={state} actions={actions} />}
    />
  );
}

export const PreviewUpdateSwitchedOnThePreview: Story = {
  args: { meta: UPDATE_META },
  render: (args) => <SwitchingOnThePreview {...args} />,
  play: async (context) => {
    await meta.play(context);
    await context.userEvent.click(await screen.findByRole("radio", { name: "Add new and update existing" }));
    await screen.findByRole("list", { name: "How updating works" });
  },
};

/** Rows are going out: numbers, the counts so far, and the instruction to keep the tab open. */
export const Importing: Story = {
  args: { state: stateFor({ phase: "importing", progress: PROGRESS_RUNNING }) },
};

/** A link or Back during an import is held: the question stacks over the dialog, with Stay focused. */
export const ImportingLeaveQuestion: Story = {
  args: { state: stateFor({ phase: "importing", progress: PROGRESS_RUNNING }) },
  render: (args) => (
    <InDataRouter>
      <PageHeader eyebrow="Records" title="Companies" actions={<ImportExportButton {...args} />} />
      <Link to="/deals">All deals</Link>
    </InDataRouter>
  ),
  play: async (context) => {
    await meta.play(context);
    // The link sits behind the dialog's scrim, so a pointer could not reach it; the story clicks it
    // to stand in for Back or a redirect, the ways a page is really left while the dialog is up.
    await context.userEvent.click(context.canvas.getByRole("link", { name: "All deals" }));
    await screen.findByRole("dialog", { name: "Stop the import and leave?" });
  },
};

/** The server asked for a pause: the wait is in words, and the import carries on by itself. */
export const ImportingWaitingForRateLimit: Story = {
  args: { state: stateFor({ phase: "importing", progress: PROGRESS_WAITING }) },
};

/** Every row went in: no table of problems, and the sentence says everything imported. */
export const DoneClean: Story = {
  args: { state: stateFor({ phase: "done", summary: SUMMARY_CLEAN }) },
};

/** An update run part way: rows created and rows updated are counted apart, and nothing is ever skipped. */
export const ImportingWithUpdates: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "importing", mode: "addAndUpdate", progress: PROGRESS_UPDATING }),
  },
};

/** Rows that already existed, that the server refused and that were invalid, each with its reason. */
export const DoneWithFailures: Story = {
  args: { state: stateFor({ phase: "done", summary: SUMMARY_WITH_PROBLEMS }) },
};

/** An update run: three updated, two created, five unchanged and one refused, in counts, with the refused row listed. */
export const DoneWithUpdatesAndFailures: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "done", mode: "addAndUpdate", summary: SUMMARY_UPDATE_WITH_FAILURES }),
  },
};

/** A row whose first request went through and a later one failed: it says which columns were saved and which were not. */
export const DoneWithAPartlySavedRow: Story = {
  args: {
    meta: UPDATE_META,
    state: stateFor({ phase: "done", mode: "addAndUpdate", summary: SUMMARY_UPDATE_PARTLY_SAVED }),
  },
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

function LaunchedFromTheMenu(args: ImportExportButtonProps) {
  const [open, setOpen] = useState(false);
  return (
    <PageHeader
      eyebrow="Records"
      title="Companies"
      secondaryActions={[{ label: "Import / Export", onSelect: () => setOpen(true) }]}
      actions={<ImportExportButton {...args} open={open} onOpenChange={setOpen} hideTrigger />}
    />
  );
}

/** Another control launches the dialog (a phone's "More actions" item); the button itself is not drawn. */
export const LaunchedFromAMenuItem: Story = {
  render: (args) => <LaunchedFromTheMenu {...args} />,
  play: async ({ canvas, userEvent }) => {
    // From 640px the action is a button in the header row; below it, an item in "More actions".
    const button = canvas.queryByRole("button", { name: "Import / Export" });
    if (button) {
      await userEvent.click(button);
    } else {
      await userEvent.click(canvas.getByRole("button", { name: "More actions" }));
      await userEvent.click(await screen.findByRole("menuitem", { name: "Import / Export" }));
    }
    await screen.findByRole("dialog");
  },
};
