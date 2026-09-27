/**
 * The Import / Export button is one control and six screens, and what matters is what a person
 * can do on each: which actions they are offered, what happens when they take one, and that
 * nothing they need is reachable only by mouse. The state is the caller's (a data-exchange hook),
 * so these tests hold it in props and change it by hand, the way the hook would.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ImportExportMeta, ImportExportViewActions, ImportExportViewState } from "../lib/importExportView";
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
import { ImportExportButton } from "./ImportExportButton";

function makeActions(): ImportExportViewActions {
  return {
    setMode: vi.fn(async () => undefined),
    openFile: vi.fn(async () => undefined),
    startImport: vi.fn(async () => undefined),
    cancelImport: vi.fn(),
    reset: vi.fn(),
    downloadTemplate: vi.fn(async () => undefined),
    exportAll: vi.fn(async () => undefined),
    cancelExport: vi.fn(),
    downloadReport: vi.fn(async () => undefined),
  };
}

interface HarnessProps {
  state?: ImportExportViewState;
  actions: ImportExportViewActions;
  meta?: ImportExportMeta;
  canImport?: boolean;
  canExport?: boolean;
}

function Harness({ state = MENU_STATE, actions, meta = META, canImport = true, canExport = true }: HarnessProps) {
  return <ImportExportButton meta={meta} state={state} actions={actions} canImport={canImport} canExport={canExport} />;
}

function xlsx(name = "companies.xlsx"): File {
  return new File(["x"], name, { type: XLSX_TYPE });
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>("input[type=file]");
  if (input === null) throw new Error("no file input");
  return input;
}

function dialog(name: string | RegExp) {
  return screen.getByRole("dialog", { name });
}

/** The figure beside a count's label, from the term and its definition, whatever else shares the word. */
function figure(label: string): string | null | undefined {
  return screen.getAllByRole("term").find((term) => term.textContent === label)?.nextElementSibling?.textContent;
}

function descriptionOf(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ");
}

/** Renders the button and opens its dialog, as a person does. */
async function openWith(props: Partial<HarnessProps> = {}, options?: Parameters<typeof userEvent.setup>[0]) {
  const user = userEvent.setup(options);
  const actions = props.actions ?? makeActions();
  const view = render(<Harness {...props} actions={actions} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  const show = (state: ImportExportViewState) => view.rerender(<Harness {...props} state={state} actions={actions} />);
  return { user, actions, show };
}

describe("ImportExportButton: the button", () => {
  it("is one secondary button that opens a dialog and nothing else", async () => {
    const user = userEvent.setup();
    render(<Harness actions={makeActions()} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    expect(dialog("Import / Export companies")).toBeTruthy();
  });

  it("renders nothing at all when the person may neither import nor export", () => {
    const { container } = render(<Harness actions={makeActions()} canImport={false} canExport={false} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when they may not import and the list cannot be exported", () => {
    const { container } = render(
      <Harness actions={makeActions()} canImport={false} meta={{ ...META, canExport: false }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("puts focus on the first action when it opens", async () => {
    await openWith();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Download sample sheet" }));
  });

  it("opens and closes from the keyboard, and gives focus back to the button", async () => {
    const user = userEvent.setup();
    const actions = makeActions();
    render(<Harness actions={actions} />);
    const trigger = screen.getByRole("button", { name: "Import / Export" });

    trigger.focus();
    await user.keyboard("{Enter}");
    expect(dialog("Import / Export companies")).toBeTruthy();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(actions.reset).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(trigger);
  });

  it("resets the session when it is closed with the close control", async () => {
    const { user, actions } = await openWith();
    await user.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(actions.reset).toHaveBeenCalledTimes(1);
  });

  it("stops a running export when the dialog is closed over it", async () => {
    const { user, actions } = await openWith({ state: stateFor({ exporting: { fetched: 40, total: 120 } }) });
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(actions.cancelExport).toHaveBeenCalledTimes(1);
    expect(actions.reset).toHaveBeenCalledTimes(1);
  });
});

describe("ImportExportButton: the menu", () => {
  it("offers the three actions, each a button with a sentence under it", async () => {
    await openWith();
    for (const [name, words] of [
      ["Download sample sheet", "columns, example rows and instructions for companies"],
      ["Import from Excel", ".xlsx file"],
      ["Export to Excel", "matching your current filters"],
    ] as const) {
      const button = screen.getByRole("button", { name });
      expect(descriptionOf(button)).toContain(words);
    }
  });

  it("explains the rules in plain words", async () => {
    await openWith();
    const rules = within(screen.getByRole("list", { name: "How importing and exporting work" }));
    expect(rules.getByText(/Up to 1.?000 rows per file/)).toBeTruthy();
    expect(rules.getByText("Rows that already exist are skipped, never overwritten.")).toBeTruthy();
    expect(rules.getByText(/read in your browser/)).toBeTruthy();
    expect(rules.getByText("Required columns: Name.")).toBeTruthy();
    expect(rules.getByText(/Only \.xlsx files work/)).toBeTruthy();
    expect(rules.getByText(/Export includes up to 10.?000 rows matching the current filters/)).toBeTruthy();
  });

  it("calls the sample sheet, the file chooser and the export from their own buttons", async () => {
    const { user, actions } = await openWith();
    const pick = vi.spyOn(fileInput(), "click");

    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    expect(actions.downloadTemplate).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Import from Excel" }));
    expect(pick).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Export to Excel" }));
    expect(actions.exportAll).toHaveBeenCalledTimes(1);
  });

  it("hides the sample sheet and Import from someone who may not create records", async () => {
    await openWith({ canImport: false });
    expect(screen.queryByRole("button", { name: "Download sample sheet" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import from Excel" })).toBeNull();
    expect(screen.getByRole("button", { name: "Export to Excel" })).toBeTruthy();
    // The rules that belong to importing go with it.
    expect(screen.queryByText(/rows per file/)).toBeNull();
    expect(screen.getByText(/Export includes up to/)).toBeTruthy();
    // Export is the only action, so it is the first one.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Export to Excel" }));
  });

  it("hides Export from someone who may not read the list", async () => {
    await openWith({ canExport: false });
    expect(screen.queryByRole("button", { name: "Export to Excel" })).toBeNull();
    expect(screen.getByRole("button", { name: "Import from Excel" })).toBeTruthy();
    expect(screen.queryByText(/Export includes up to/)).toBeNull();
  });

  it("hides Export when the list cannot be exported at all", async () => {
    await openWith({ meta: { ...META, canExport: false } });
    expect(screen.queryByRole("button", { name: "Export to Excel" })).toBeNull();
    expect(screen.getByRole("button", { name: "Import from Excel" })).toBeTruthy();
  });

  it("says the sample sheet is being built, and does not start a second one", async () => {
    const { user, actions } = await openWith({ state: stateFor({ downloadingTemplate: true }) });
    const button = screen.getByRole("button", { name: "Download sample sheet" });
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(descriptionOf(button)).toBe("Preparing the sample sheet…");

    await user.click(button);
    expect(actions.downloadTemplate).not.toHaveBeenCalled();
    // It is aria-disabled, not disabled, so the keyboard user who started it still has focus.
    expect(button.hasAttribute("disabled")).toBe(false);
  });

  it("shows how far the export has got, and cancels it from a button beside the bar", async () => {
    const { user, actions } = await openWith({ state: stateFor({ exporting: { fetched: 40, total: 120 } }) });
    const row = screen.getByRole("button", { name: "Export to Excel" });
    expect(descriptionOf(row)).toBe("Exported 40 of 120");
    expect(row.getAttribute("aria-busy")).toBe("true");

    const bar = screen.getByRole("progressbar", { name: "Export progress" });
    expect(bar.getAttribute("aria-valuenow")).toBe("40");
    expect(bar.getAttribute("aria-valuemax")).toBe("120");
    expect(bar.getAttribute("aria-valuetext")).toBe("Exported 40 of 120");

    await user.click(row);
    expect(actions.exportAll).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Cancel export" }));
    expect(actions.cancelExport).toHaveBeenCalledTimes(1);
    // The Cancel button leaves; focus stays with the export it belonged to.
    expect(document.activeElement).toBe(row);
  });

  it("says so while the export is still counting what it will fetch", async () => {
    await openWith({ state: stateFor({ exporting: { fetched: 0, total: 0 } }) });
    expect(descriptionOf(screen.getByRole("button", { name: "Export to Excel" }))).toBe("Preparing the export…");
    expect(screen.getByRole("progressbar", { name: "Export progress" }).hasAttribute("aria-valuenow")).toBe(false);
  });

  it("shows the reason a download failed, as an alert", async () => {
    await openWith({ state: stateFor({ notice: "The export failed: the list could not be read. Try again." }) });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("The export failed: the list could not be read. Try again.");
  });
});

describe("ImportExportButton: choosing and dropping a file", () => {
  it("reads the file chosen in the picker, whatever it is", async () => {
    const { user, actions } = await openWith({}, { applyAccept: false });
    const notes = new File(["x"], "notes.txt", { type: "text/plain" });
    await user.upload(fileInput(), notes);
    // The engine is the judge of the file; the dialog does not turn one away.
    expect(actions.openFile).toHaveBeenCalledWith(notes);
  });

  it("asks the picker for Excel workbooks", async () => {
    await openWith();
    expect(fileInput().accept).toContain(".xlsx");
  });

  it("reads a file dropped on the dialog, and shows where it will land while it is dragged over", async () => {
    const { actions } = await openWith();
    const target = screen.getByRole("button", { name: "Import from Excel" });
    const file = xlsx();

    fireEvent.dragEnter(target, { dataTransfer: { types: ["Files"], files: [file] } });
    expect(descriptionOf(target)).toBe("Drop the file to read it.");

    fireEvent.drop(target, { dataTransfer: { types: ["Files"], files: [file] } });
    expect(actions.openFile).toHaveBeenCalledWith(file);
    expect(descriptionOf(target)).toContain("Choose a file or drop it here");
  });

  it("keeps the drop cue while the drag moves between the dialog's own parts, and clears it when the drag leaves", async () => {
    await openWith();
    const target = screen.getByRole("button", { name: "Import from Excel" });
    const other = screen.getByRole("button", { name: "Export to Excel" });
    const drag = { dataTransfer: { types: ["Files"], files: [] } };

    fireEvent.dragEnter(target, drag);
    fireEvent.dragEnter(other, drag);
    fireEvent.dragLeave(target, drag);
    expect(descriptionOf(target)).toBe("Drop the file to read it.");

    fireEvent.dragLeave(other, drag);
    expect(descriptionOf(target)).toContain("Choose a file or drop it here");
  });

  it("passes on a dropped file that is not a workbook, too", async () => {
    const { actions } = await openWith();
    const notes = new File(["x"], "notes.txt", { type: "text/plain" });
    fireEvent.drop(screen.getByRole("button", { name: "Import from Excel" }), {
      dataTransfer: { types: ["Files"], files: [notes] },
    });
    expect(actions.openFile).toHaveBeenCalledWith(notes);
  });

  it("ignores a drag that carries no file", async () => {
    const { actions } = await openWith();
    const target = screen.getByRole("button", { name: "Import from Excel" });
    fireEvent.dragEnter(target, { dataTransfer: { types: ["text/plain"], files: [] } });
    expect(descriptionOf(target)).toContain("Choose a file or drop it here");
    fireEvent.drop(target, { dataTransfer: { types: ["text/plain"], files: [] } });
    expect(actions.openFile).not.toHaveBeenCalled();
  });

  it("takes no file from someone who may not import", async () => {
    const { actions } = await openWith({ canImport: false });
    fireEvent.drop(screen.getByRole("button", { name: "Export to Excel" }), {
      dataTransfer: { types: ["Files"], files: [xlsx()] },
    });
    expect(actions.openFile).not.toHaveBeenCalled();
  });
});

describe("ImportExportButton: a file dropped outside the drop zone", () => {
  /** What a drag from the desktop carries. `dropEffect` is what the page has answered so far. */
  function fileDrag(dropEffect = "copy") {
    return { types: ["Files"], files: [xlsx()], dropEffect };
  }

  function backdropOf(name: string) {
    const backdrop = dialog(name).previousElementSibling;
    if (backdrop === null) throw new Error("no backdrop");
    return backdrop;
  }

  it("does nothing when the file is dropped on the backdrop, the header or the footer, and the browser does not open it", async () => {
    const { actions } = await openWith();
    const outside = [
      backdropOf("Import / Export companies"),
      screen.getByRole("heading", { name: "Import / Export companies" }),
      screen.getByRole("button", { name: "Close" }),
      screen.getByRole("button", { name: "Close dialog" }),
    ];
    for (const target of outside) {
      const dropped = fireEvent.drop(target, { dataTransfer: fileDrag() });
      // Cancelled: the default, opening the file in this tab, did not happen.
      expect(dropped).toBe(false);
    }
    expect(actions.openFile).not.toHaveBeenCalled();
  });

  it("does nothing when the file is dropped on the page itself", async () => {
    const { actions } = await openWith();
    expect(fireEvent.drop(document.body, { dataTransfer: fileDrag() })).toBe(false);
    expect(actions.openFile).not.toHaveBeenCalled();
  });

  it("says nothing will happen while the file is dragged over anywhere but the zone", async () => {
    await openWith();
    for (const target of [backdropOf("Import / Export companies"), screen.getByRole("button", { name: "Close" })]) {
      const transfer = fileDrag();
      // Cancelled, so the page is a valid target and the browser has no default to fall back to.
      expect(fireEvent.dragOver(target, { dataTransfer: transfer })).toBe(false);
      expect(transfer.dropEffect).toBe("none");
    }
  });

  it("still takes the file on the zone, and leaves the drop effect the zone chose", async () => {
    const { actions } = await openWith();
    const target = screen.getByRole("button", { name: "Import from Excel" });

    const transfer = fileDrag("none");
    fireEvent.dragOver(target, { dataTransfer: transfer });
    expect(transfer.dropEffect).toBe("copy");

    const file = xlsx("corrected.xlsx");
    expect(fireEvent.drop(target, { dataTransfer: { types: ["Files"], files: [file] } })).toBe(false);
    expect(actions.openFile).toHaveBeenCalledTimes(1);
    expect(actions.openFile).toHaveBeenCalledWith(file);
  });

  it("does the same in every phase: a drop on the header during an import is cancelled and read by no one", async () => {
    const { actions } = await openWith({ state: stateFor({ phase: "importing", progress: PROGRESS_RUNNING }) });
    const header = screen.getByRole("heading", { name: "Importing companies" });
    expect(fireEvent.dragOver(header, { dataTransfer: fileDrag() })).toBe(false);
    expect(fireEvent.drop(header, { dataTransfer: fileDrag() })).toBe(false);
    expect(fireEvent.drop(backdropOf("Importing companies"), { dataTransfer: fileDrag() })).toBe(false);
    expect(actions.openFile).not.toHaveBeenCalled();
  });

  it("leaves a drag that carries no file alone", async () => {
    await openWith();
    const text = { types: ["text/plain"], files: [], dropEffect: "copy" };
    expect(fireEvent.dragOver(backdropOf("Import / Export companies"), { dataTransfer: text })).toBe(true);
    expect(fireEvent.drop(backdropOf("Import / Export companies"), { dataTransfer: text })).toBe(true);
    expect(text.dropEffect).toBe("copy");
  });

  it("only listens while the dialog is open, and lets go of both listeners when it closes", async () => {
    const remove = vi.spyOn(window, "removeEventListener");
    const add = vi.spyOn(window, "addEventListener");
    const user = userEvent.setup();
    render(<Harness actions={makeActions()} />);
    // Closed: the page is the browser's again.
    expect(fireEvent.drop(document.body, { dataTransfer: fileDrag() })).toBe(true);

    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    expect(add.mock.calls.filter(([type]) => type === "dragover" || type === "drop")).toHaveLength(2);
    expect(fireEvent.drop(document.body, { dataTransfer: fileDrag() })).toBe(false);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(remove.mock.calls.filter(([type]) => type === "dragover" || type === "drop")).toHaveLength(2);
    expect(fireEvent.dragOver(document.body, { dataTransfer: fileDrag() })).toBe(true);
    expect(fireEvent.drop(document.body, { dataTransfer: fileDrag() })).toBe(true);
  });

  it("lets go of them when the dialog is unmounted with the page", async () => {
    const view = render(<Harness actions={makeActions()} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Import / Export" }));
    view.unmount();
    expect(fireEvent.drop(document.body, { dataTransfer: fileDrag() })).toBe(true);
  });
});

describe("ImportExportButton: reading", () => {
  it("says the file is being read, without a control that could interrupt it", async () => {
    await openWith({ state: stateFor({ phase: "reading" }) });
    expect(dialog("Reading your file")).toBeTruthy();
    // Said once, politely, for the screen reader; the words on screen say what is being checked.
    expect(screen.getByText("Reading your file…").closest("[aria-live=polite]")).toBeTruthy();
    expect(screen.getByText(/Checking the columns and every row/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Import from Excel" })).toBeNull();
  });
});

describe("ImportExportButton: the preview", () => {
  const preview = stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS });

  it("names the file and gives every count in words", async () => {
    await openWith({ state: preview });
    expect(dialog("Review your file")).toBeTruthy();
    expect(screen.getByText("companies-september.xlsx")).toBeTruthy();
    for (const [label, value] of [
      ["Rows in file", "10"],
      ["Ready to import", "5"],
      ["Need fixing", "3"],
      ["Sample rows skipped", "2"],
    ] as const) {
      expect(figure(label)).toBe(value);
    }
    expect(screen.getByText("5 rows ready to import, 3 need fixing.").closest("[aria-live=polite]")).toBeTruthy();
  });

  it("lists every row with a word for what will happen to it, the rows to fix first with the reason", async () => {
    await openWith({ state: preview });
    const table = screen.getByRole("table", { name: "Rows found in companies-september.xlsx" });
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Row", "Action", "Name", "Industry", "City", "Website", "Employees"]);

    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(11);
    const first = within(rows[1] as HTMLElement);
    expect(first.getByRole("rowheader").textContent).toBe("6");
    expect(first.getByText("Needs fixing")).toBeTruthy();
    expect(first.getByText("Name is required.")).toBeTruthy();
    expect(within(rows[4] as HTMLElement).getByText("Sample")).toBeTruthy();
    expect(within(rows[6] as HTMLElement).getByText("New")).toBeTruthy();
    expect(screen.getByText("Rows that need fixing come first.")).toBeTruthy();
  });

  it("puts the rows that cannot be sent first, whatever order they arrive in", async () => {
    const [needsFixing, , , , , ready] = PREVIEW_WITH_ERRORS.rows;
    if (needsFixing === undefined || ready === undefined) throw new Error("fixture changed");
    await openWith({
      state: stateFor({
        phase: "preview",
        preview: { ...PREVIEW_WITH_ERRORS, total: 2, valid: 1, invalid: 1, sample: 0, rows: [ready, needsFixing] },
      }),
    });
    const rows = within(screen.getByRole("table", { name: /Rows found/ })).getAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByRole("rowheader").textContent).toBe(String(needsFixing.row));
    expect(within(rows[2] as HTMLElement).getByRole("rowheader").textContent).toBe(String(ready.row));
  });

  it("makes an empty cell readable", async () => {
    await openWith({ state: preview });
    const table = screen.getByRole("table", { name: "Rows found in companies-september.xlsx" });
    const rows = within(table).getAllByRole("row");
    // Row 6's name cell was left empty.
    expect(within(rows[1] as HTMLElement).getAllByText("Empty").length).toBeGreaterThan(0);
  });

  it("says when only some of the rows are shown", async () => {
    await openWith({ state: stateFor({ phase: "preview", preview: { ...PREVIEW_WITH_ERRORS, total: 640 } }) });
    expect(screen.getByText("Showing 10 of 640 rows. Rows that need fixing come first.")).toBeTruthy();
  });

  it("notes the columns it will ignore, without blocking", async () => {
    await openWith({ state: preview });
    expect(screen.getByText("These columns are ignored: Nickname.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Import 5 rows" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables Import and says why while an earlier run's cooldown is still counting down", async () => {
    const { user, actions, show } = await openWith({
      state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS, cooldownSecondsLeft: 65 }),
    });
    expect(screen.getByText(/That was a large batch\..*you can start another import in 1:05\./)).toBeTruthy();
    const waiting = screen.getByRole("button", { name: "Wait 1:05" }) as HTMLButtonElement;
    expect(waiting.disabled).toBe(true);
    await user.click(waiting);
    expect(actions.startImport).not.toHaveBeenCalled();

    // Once the engine clears it, the usual label and count come back and Import works again.
    show(stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS, cooldownSecondsLeft: null }));
    expect(screen.queryByText(/large batch/)).toBeNull();
    const ready = screen.getByRole("button", { name: "Import 5 rows" }) as HTMLButtonElement;
    expect(ready.disabled).toBe(false);
    await user.click(ready);
    expect(actions.startImport).toHaveBeenCalledTimes(1);
  });

  it("blocks the import, and says why, when a required column is missing", async () => {
    await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_MISSING_COLUMNS }) });
    expect(screen.getByText("Your file is missing required columns: Name.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Import 0 rows" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("These columns are ignored: Nickname, Account manager.")).toBeTruthy();
  });

  it("blocks the import when nothing in the file is valid", async () => {
    await openWith({
      state: stateFor({
        phase: "preview",
        preview: { ...PREVIEW_WITH_ERRORS, valid: 0, invalid: 3, sample: 0, total: 3, rows: PREVIEW_WITH_ERRORS.rows.slice(0, 3) },
      }),
    });
    expect((screen.getByRole("button", { name: "Import 0 rows" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("No rows can be imported yet.")).toBeTruthy();
  });

  it("says when the file holds only the sample rows", async () => {
    await openWith({
      state: stateFor({
        phase: "preview",
        preview: { ...PREVIEW_WITH_ERRORS, valid: 0, invalid: 0, sample: 2, total: 2, rows: PREVIEW_WITH_ERRORS.rows.slice(3, 5) },
      }),
    });
    expect(screen.getByText("Only the sample rows are in this file.")).toBeTruthy();
  });

  it("has an empty state for a file with a header and no rows", async () => {
    await openWith({
      state: stateFor({
        phase: "preview",
        preview: { ...PREVIEW_WITH_ERRORS, total: 0, valid: 0, invalid: 0, sample: 0, rows: [] },
      }),
    });
    expect(screen.getByText("No rows found")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("sends the rows once, however many times the button is pressed", async () => {
    const actions = makeActions();
    // The real action resolves when the whole import has finished, so until then it is in flight.
    actions.startImport = vi.fn(() => new Promise<void>(() => undefined));
    const { user } = await openWith({ state: preview, actions });
    await user.dblClick(screen.getByRole("button", { name: "Import 5 rows" }));
    expect(actions.startImport).toHaveBeenCalledTimes(1);
  });

  it("says Import 1 row for one row", async () => {
    await openWith({
      state: stateFor({ phase: "preview", preview: { ...PREVIEW_WITH_ERRORS, valid: 1, invalid: 0, sample: 0, total: 1, rows: PREVIEW_WITH_ERRORS.rows.slice(5, 6) } }),
    });
    expect(screen.getByRole("button", { name: "Import 1 row" })).toBeTruthy();
  });

  it("opens the file chooser again from Choose another file, and takes a dropped file too", async () => {
    const { user, actions } = await openWith({ state: preview });
    const pick = vi.spyOn(fileInput(), "click");
    await user.click(screen.getByRole("button", { name: "Choose another file" }));
    expect(pick).toHaveBeenCalledTimes(1);

    const file = xlsx("corrected.xlsx");
    fireEvent.drop(screen.getByRole("table", { name: /Rows found/ }), { dataTransfer: { types: ["Files"], files: [file] } });
    expect(actions.openFile).toHaveBeenCalledWith(file);
  });

  it("scrolls sideways and vertically as a region a keyboard can reach", async () => {
    await openWith({ state: preview });
    const region = screen.getByRole("region", { name: "Rows found in companies-september.xlsx" });
    expect(region.getAttribute("tabindex")).toBe("0");
  });
});

describe("ImportExportButton: importing", () => {
  const importing = stateFor({ phase: "importing", progress: PROGRESS_RUNNING });

  it("shows the progress in numbers, and what has happened to the rows so far", async () => {
    await openWith({ state: importing });
    expect(dialog("Importing companies")).toBeTruthy();
    expect(screen.getByText("46 of 120 rows sent")).toBeTruthy();
    const bar = screen.getByRole("progressbar", { name: "Import progress" });
    expect(bar.getAttribute("aria-valuenow")).toBe("46");
    expect(bar.getAttribute("aria-valuemax")).toBe("120");
    expect(bar.getAttribute("aria-valuetext")).toBe("46 of 120 rows sent");
    for (const [label, value] of [
      ["Created", "40"],
      ["Skipped, already exist", "4"],
      ["Failed", "2"],
    ] as const) {
      expect(figure(label)).toBe(value);
    }
    expect(screen.getByText(/Keep this tab open/)).toBeTruthy();
  });

  it("says the wait for the rate limit in words, with the seconds", async () => {
    const { show } = await openWith({ state: stateFor({ phase: "importing", progress: PROGRESS_WAITING }) });
    expect(screen.getByText("Waiting for the rate limit… 42 s")).toBeTruthy();
    show(stateFor({ phase: "importing", progress: { ...PROGRESS_WAITING, waiting: { reason: "rate-limit", seconds: 41 } } }));
    expect(screen.getByText("Waiting for the rate limit… 41 s")).toBeTruthy();
  });

  it("copes with the moment before the first report", async () => {
    await openWith({ state: stateFor({ phase: "importing", progress: null }) });
    expect(screen.getByText("Starting the import…")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Import progress" }).hasAttribute("aria-valuenow")).toBe(false);
  });

  it("announces in tenths, not once per row, and not with the countdown in it", async () => {
    const polite = () => document.querySelector("[aria-live=polite]")?.textContent;
    const at = (done: number, waiting: { reason: "rate-limit"; seconds: number } | null = null) =>
      stateFor({ phase: "importing", progress: { ...PROGRESS_RUNNING, done, waiting } });
    const { show } = await openWith({ state: at(0) });
    expect(polite()).toBe("Import started.");

    show(at(14));
    expect(polite()).toBe("Importing: 10% of rows sent.");
    show(at(15));
    show(at(23));
    expect(polite()).toBe("Importing: 10% of rows sent.");
    show(at(24));
    expect(polite()).toBe("Importing: 20% of rows sent.");

    show(at(24, { reason: "rate-limit", seconds: 42 }));
    expect(polite()).toBe("Waiting for the rate limit. The import continues by itself.");
    show(at(24, { reason: "rate-limit", seconds: 41 }));
    expect(polite()).toBe("Waiting for the rate limit. The import continues by itself.");
  });

  it("cancels, and says it is stopping while it finishes the row in flight", async () => {
    const { user, actions } = await openWith({ state: importing });
    await user.click(screen.getByRole("button", { name: "Cancel import" }));
    expect(actions.cancelImport).toHaveBeenCalledTimes(1);
    const stopping = screen.getByRole("button", { name: "Stopping…" });
    expect(stopping.getAttribute("aria-busy")).toBe("true");
    await user.click(stopping);
    expect(actions.cancelImport).toHaveBeenCalledTimes(1);
  });

  it("cannot be dismissed with Escape, the close control or the backdrop, and says why", async () => {
    const { user, actions } = await openWith({ state: importing });
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("");

    await user.keyboard("{Escape}");
    expect(dialog("Importing companies")).toBeTruthy();
    expect(status.textContent).toBe("The import is still running. Choose Cancel import to stop it.");

    await user.click(screen.getByRole("button", { name: "Close dialog" }));
    // The backdrop is the first, aria-hidden child of the dialog's overlay.
    const backdrop = dialog("Importing companies").previousElementSibling;
    if (backdrop === null) throw new Error("no backdrop");
    await user.click(backdrop);

    expect(dialog("Importing companies")).toBeTruthy();
    expect(actions.reset).not.toHaveBeenCalled();
  });

  it("asks the browser before the tab is closed while rows are going out, and stops asking after", async () => {
    const { show } = await openWith({ state: importing });
    const leaving = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leaving);
    expect(leaving.defaultPrevented).toBe(true);

    show(stateFor({ phase: "done", summary: SUMMARY_CLEAN }));
    const after = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });

  it("refuses a file dropped on it, because the browser would navigate to the file and end the import", async () => {
    const { actions } = await openWith({ state: importing });
    const dropTarget = screen.getByRole("progressbar", { name: "Import progress" });
    const over = fireEvent.dragOver(dropTarget, { dataTransfer: { types: ["Files"], files: [xlsx()] } });
    const dropped = fireEvent.drop(dropTarget, { dataTransfer: { types: ["Files"], files: [xlsx()] } });
    // Cancelled: the default, navigating to the file, did not happen.
    expect(over).toBe(false);
    expect(dropped).toBe(false);
    expect(actions.openFile).not.toHaveBeenCalled();
  });
});

describe("ImportExportButton: done", () => {
  it("says everything imported when nothing went wrong, with no table of problems", async () => {
    await openWith({ state: stateFor({ phase: "done", summary: SUMMARY_CLEAN }) });
    expect(dialog("Import finished")).toBeTruthy();
    expect(screen.getByText("Everything imported. 6 companies were created.")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(figure("Created")).toBe("6");
    // The counts are announced once, and not the sentence that is already on the page.
    expect(document.querySelector("[aria-live=polite]")?.textContent).toBe(
      "6 created, 0 already existed, 0 failed, 0 invalid.",
    );
  });

  it("counts created, skipped, failed and invalid, and lists every row that was not created", async () => {
    await openWith({ state: stateFor({ phase: "done", summary: SUMMARY_WITH_PROBLEMS }) });
    expect(
      screen.getByText("3 rows were not imported. 4 companies were created."),
    ).toBeTruthy();
    for (const [label, value] of [
      ["Created", "4"],
      ["Skipped, already exist", "2"],
      ["Failed", "2"],
      ["Invalid", "1"],
    ] as const) {
      expect(figure(label)).toBe(value);
    }

    expect(screen.getByText("5 rows were not created. The result report lists every row.")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Rows that were not created" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(6);
    // A created row is not a problem, so it is not here.
    expect(within(table).queryByText("Acme Industries")).toBeNull();

    const lotus = within(rows.find((row) => within(row).queryByText("Lotus Packaging") !== null) as HTMLElement);
    expect(lotus.getByText("Failed")).toBeTruthy();
    expect(lotus.getByText("You can't assign Priya Raman as the owner of a company.")).toBeTruthy();
    const northwind = within(rows.find((row) => within(row).queryByText("Northwind Traders") !== null) as HTMLElement);
    expect(northwind.getByText("Skipped")).toBeTruthy();
    expect(northwind.getByText("A company with this name already exists.")).toBeTruthy();
    const harbor = within(rows.find((row) => within(row).queryByText("Harbor Exports") !== null) as HTMLElement);
    expect(harbor.getByText("Invalid")).toBeTruthy();
  });

  it("says it was cancelled, and that the remaining rows were not sent", async () => {
    await openWith({ state: stateFor({ phase: "done", summary: SUMMARY_CANCELLED }) });
    expect(screen.getByRole("heading", { name: "Import cancelled" })).toBeTruthy();
    expect(
      screen.getByText("2 companies were created before it stopped. The remaining rows were not sent."),
    ).toBeTruthy();
  });

  it("downloads the report, and shows the reason when it cannot", async () => {
    const { user, actions, show } = await openWith({ state: stateFor({ phase: "done", summary: SUMMARY_WITH_PROBLEMS }) });
    await user.click(screen.getByRole("button", { name: "Download result report" }));
    expect(actions.downloadReport).toHaveBeenCalledTimes(1);

    show(stateFor({ phase: "done", summary: SUMMARY_WITH_PROBLEMS, notice: "The report could not be built. Try again." }));
    expect(screen.getByRole("alert").textContent).toContain("The report could not be built. Try again.");
  });

  it("resets and closes on Done, with focus back on the button", async () => {
    const { user, actions } = await openWith({ state: stateFor({ phase: "done", summary: SUMMARY_CLEAN }) });
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(actions.reset).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Import / Export" }));
  });
});

describe("ImportExportButton: a file that cannot be used", () => {
  const failed = stateFor({ phase: "error", error: "That file is 7 MB. The limit is 5 MB." });

  it("shows the reason in an alert, with Try again", async () => {
    const { user, actions } = await openWith({ state: failed });
    expect(dialog("We couldn't read that file")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("That file is 7 MB. The limit is 5 MB.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(actions.reset).toHaveBeenCalledTimes(1);
  });

  it("still says something when the engine gave no sentence", async () => {
    await openWith({ state: stateFor({ phase: "error", error: null }) });
    expect(screen.getByRole("alert").textContent).toContain("Something went wrong while reading your file.");
  });

  it("closes from the footer", async () => {
    const { user, actions } = await openWith({ state: failed });
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(actions.reset).toHaveBeenCalledTimes(1);
  });
});

describe("ImportExportButton: focus and the keyboard", () => {
  it("moves focus to the new heading when the phase changes, so it is not left on a control that left", async () => {
    const { show } = await openWith();
    const downloadSample = screen.getByRole("button", { name: "Download sample sheet" });
    expect(document.activeElement).toBe(downloadSample);

    show(stateFor({ phase: "reading" }));
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Reading your file" }));

    show(stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }));
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Review your file" }));

    show(stateFor({ phase: "importing", progress: PROGRESS_RUNNING }));
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Importing companies" }));

    show(stateFor({ phase: "done", summary: SUMMARY_CLEAN }));
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Import finished" }));

    show(MENU_STATE);
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Import / Export companies" }));
  });

  it("does not move focus when a phase's numbers change", async () => {
    const { show } = await openWith({ state: stateFor({ phase: "importing", progress: PROGRESS_RUNNING }) });
    const cancel = screen.getByRole("button", { name: "Cancel import" });
    cancel.focus();
    show(stateFor({ phase: "importing", progress: { ...PROGRESS_RUNNING, done: 47 } }));
    expect(document.activeElement).toBe(cancel);
  });

  it("can be worked through with the keyboard alone, from the first click to Done", async () => {
    const user = userEvent.setup();
    const actions = makeActions();
    const view = render(<Harness actions={actions} />);
    const show = (state: ImportExportViewState) => view.rerender(<Harness state={state} actions={actions} />);

    screen.getByRole("button", { name: "Import / Export" }).focus();
    await user.keyboard("{Enter}");

    // The first action is under the fingers: Enter takes the sample sheet.
    await user.keyboard("{Enter}");
    expect(actions.downloadTemplate).toHaveBeenCalledTimes(1);

    // Space on the primary action opens the chooser.
    const pick = vi.spyOn(fileInput(), "click");
    screen.getByRole("button", { name: "Import from Excel" }).focus();
    await user.keyboard(" ");
    expect(pick).toHaveBeenCalledTimes(1);

    show(stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }));
    screen.getByRole("button", { name: "Import 5 rows" }).focus();
    await user.keyboard("{Enter}");
    expect(actions.startImport).toHaveBeenCalledTimes(1);

    show(stateFor({ phase: "importing", progress: PROGRESS_RUNNING }));
    screen.getByRole("button", { name: "Cancel import" }).focus();
    await user.keyboard("{Enter}");
    expect(actions.cancelImport).toHaveBeenCalledTimes(1);

    show(stateFor({ phase: "done", summary: SUMMARY_CANCELLED }));
    screen.getByRole("button", { name: "Done" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(actions.reset).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Import / Export" }));
  });
});
