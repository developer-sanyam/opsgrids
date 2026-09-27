/**
 * Update mode in the Import / Export dialog: the choice between adding only and adding and
 * updating, what a preview says about a row that changes a record, and what an update run reports.
 * The state is the caller's (a data-exchange hook), so these tests hold it in props and change it by
 * hand, the way the hook would.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { updateRules } from "../lib/importExportCopy";
import type { ImportExportMeta, ImportExportViewActions, ImportExportViewState } from "../lib/importExportView";
import {
  META,
  PREVIEW_UPDATE_ALL_UNCHANGED,
  PREVIEW_UPDATE_CANNOT_MATCH,
  PREVIEW_UPDATE_MIXED,
  PREVIEW_UPDATE_NO_BASELINE,
  PREVIEW_UPDATE_REFUSALS,
  PREVIEW_UPDATE_ROWS_WITHOUT_ID,
  PREVIEW_UPDATE_UNUSABLE_BASELINE,
  PREVIEW_WITH_ERRORS,
  PROGRESS_UPDATING,
  SUMMARY_UPDATE_CLEAN,
  SUMMARY_UPDATE_PARTLY_SAVED,
  SUMMARY_UPDATE_WITH_FAILURES,
  SUMMARY_WITH_PROBLEMS,
  UPDATE_META,
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

function Harness({ state = stateFor({}), actions, meta = UPDATE_META, canImport = true, canExport = true }: HarnessProps) {
  return <ImportExportButton meta={meta} state={state} actions={actions} canImport={canImport} canExport={canExport} />;
}

/** Renders the button and opens its dialog, as a person does. */
async function openWith(props: Partial<HarnessProps> = {}) {
  const user = userEvent.setup();
  const actions = props.actions ?? makeActions();
  const view = render(<Harness {...props} actions={actions} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  const show = (state: ImportExportViewState) => view.rerender(<Harness {...props} state={state} actions={actions} />);
  return { user, actions, show };
}

function figure(label: string): string | null | undefined {
  return screen.getAllByRole("term").find((term) => term.textContent === label)?.nextElementSibling?.textContent;
}

const updating = (overrides: Partial<ImportExportViewState>) => stateFor({ mode: "addAndUpdate", ...overrides });

describe("the choice between adding and updating, in the menu", () => {
  it("is a group of two radios, adding only chosen, between the sample sheet and Import", async () => {
    await openWith();
    const group = screen.getByRole("group", { name: "What should the import do?" });
    const [add, update] = within(group).getAllByRole("radio") as HTMLInputElement[];
    expect(add?.labels?.[0]?.textContent).toBe("Add new records only");
    expect(update?.labels?.[0]?.textContent).toBe("Add new and update existing");
    expect(add?.checked).toBe(true);
    expect(update?.checked).toBe(false);

    const order = [
      screen.getByRole("button", { name: "Download sample sheet" }),
      group,
      screen.getByRole("button", { name: "Import from Excel" }),
    ];
    expect(order[0]?.compareDocumentPosition(order[1] as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(order[1]?.compareDocumentPosition(order[2] as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("is not there for a list that cannot be updated", async () => {
    await openWith({ meta: META });
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByRole("group", { name: "What should the import do?" })).toBeNull();
  });

  it("is not there for someone who may not import, who cannot update either", async () => {
    await openWith({ canImport: false });
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByRole("button", { name: "Export to Excel" })).toBeTruthy();
  });

  it("says nothing about updating until it is chosen", async () => {
    await openWith();
    expect(screen.queryByRole("list", { name: "How updating works" })).toBeNull();
    expect(screen.getByText("Rows that already exist are skipped, never overwritten.")).toBeTruthy();
  });

  it("asks for the mode the person picks, with a click and with the keyboard", async () => {
    const { user, actions } = await openWith();
    await user.click(screen.getByRole("radio", { name: "Add new and update existing" }));
    expect(actions.setMode).toHaveBeenCalledTimes(1);
    expect(actions.setMode).toHaveBeenLastCalledWith("addAndUpdate");

    screen.getByRole("radio", { name: "Add new and update existing" }).focus();
    await user.keyboard(" ");
    expect(actions.setMode).toHaveBeenCalledTimes(2);
    expect(actions.setMode).toHaveBeenLastCalledWith("addAndUpdate");
  });

  it("is reachable by keyboard: real radios in a fieldset, none taken out of the tab order", async () => {
    await openWith();
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios).toHaveLength(2);
    for (const radio of radios) {
      expect(radio.tabIndex).toBe(0);
      expect(radio.disabled).toBe(false);
      expect(radio.closest("fieldset")).toBe(screen.getByRole("group", { name: "What should the import do?" }));
    }
  });

  it("shows the rules of updating under the choice once it is chosen, and drops the rule they contradict", async () => {
    await openWith({ state: updating({}) });
    expect(document.querySelector("details")).toBeNull();
    expect(screen.getByRole("radio", { name: "Add new and update existing" })).toHaveProperty("checked", true);
    const rules = within(screen.getByRole("list", { name: "How updating works" }));
    for (const rule of updateRules(UPDATE_META.recordIdHeader)) expect(rules.getByText(rule)).toBeTruthy();
    expect(rules.getByText(/\[clear\]/)).toBeTruthy();
    expect(screen.queryByText("Rows that already exist are skipped, never overwritten.")).toBeNull();
    expect(screen.getByText(/Up to 1.?000 rows per file/)).toBeTruthy();
  });

  it("says the file will add and update", async () => {
    await openWith({ state: updating({}) });
    const button = screen.getByRole("button", { name: "Import from Excel" });
    const description = (button.getAttribute("aria-describedby") ?? "")
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ");
    expect(description).toContain("Add and update items from an .xlsx file");
  });
});

describe("the choice on the preview", () => {
  it("is there, so the same file can be checked again in the other mode", async () => {
    const { user, actions } = await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    expect(screen.getByRole("radio", { name: "Add new records only" })).toHaveProperty("checked", true);
    await user.click(screen.getByRole("radio", { name: "Add new and update existing" }));
    expect(actions.setMode).toHaveBeenCalledWith("addAndUpdate");
  });

  it("keeps focus on the radio the person chose when the file is checked again at once", async () => {
    const { user, show } = await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    await user.click(screen.getByRole("radio", { name: "Add new and update existing" }));
    const chosen = screen.getByRole("radio", { name: "Add new and update existing" });
    chosen.focus();
    show(updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED }));
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Add new and update existing" }));
    expect(screen.getByRole("radio", { name: "Add new and update existing" })).toHaveProperty("checked", true);
  });

  it("keeps the rules of updating behind their own heading, closed, so the rows stay in view", async () => {
    const { user } = await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED }) });
    const summary = screen.getByText("How updating works", { selector: "summary" });
    const disclosure = summary.closest("details");
    expect(disclosure?.open).toBe(false);
    await user.click(summary);
    expect(disclosure?.open).toBe(true);
    expect(within(disclosure as HTMLElement).getByText(/Type \[clear\] in a cell to empty a field/)).toBeTruthy();
  });

  it("opens the rules of updating when updating is switched to on the preview itself: they were never on screen", async () => {
    const { user, show } = await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    await user.click(screen.getByRole("radio", { name: "Add new and update existing" }));
    show(updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED }));

    expect(document.querySelector("details")).toBeNull();
    const rules = within(screen.getByRole("list", { name: "How updating works" }));
    for (const rule of updateRules(UPDATE_META.recordIdHeader)) expect(rules.getByText(rule)).toBeTruthy();
    // The overwrite warning is in plain sight, not behind a heading.
    expect(rules.getByText(/The update cannot detect that someone else changed that field after your export/)).toBeTruthy();
  });

  it("still opens them when the file is checked again first and the preview is shown a moment later", async () => {
    const { user, show } = await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    await user.click(screen.getByRole("radio", { name: "Add new and update existing" }));
    show(updating({ phase: "reading" }));
    show(updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED }));
    expect(screen.getByRole("list", { name: "How updating works" })).toBeTruthy();
    expect(document.querySelector("details")).toBeNull();
  });

  it("keeps them closed again for the next file once the dialog is back at the menu", async () => {
    const { user, show } = await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    await user.click(screen.getByRole("radio", { name: "Add new and update existing" }));
    show(updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED }));
    show(stateFor({ phase: "menu" }));
    show(updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED }));
    expect(document.querySelector("details")?.open).toBe(false);
  });

  it("does not show the rules at all while adding only is chosen", async () => {
    await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    expect(screen.queryByText("How updating works")).toBeNull();
  });

  it("is not offered for a list that cannot be updated", async () => {
    await openWith({ meta: META, state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("is gone while the file is checked again, which says what it is doing", async () => {
    await openWith({ state: updating({ phase: "reading" }) });
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByText(/Loading the current records and comparing every row with them/)).toBeTruthy();
  });
});

describe("the preview of an update", () => {
  const mixed = updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED });

  it("gives every count in words", async () => {
    await openWith({ state: mixed });
    for (const [label, value] of [
      ["Rows in file", "8"],
      ["New records", "2"],
      ["Updates", "3"],
      ["No change", "2"],
      ["Need fixing", "1"],
      ["Sample rows skipped", "0"],
    ] as const) {
      expect(figure(label)).toBe(value);
    }
    expect(
      screen.getByText("5 rows ready to import (3 to update, 2 new), 2 unchanged, 1 needs fixing.").closest("[aria-live=polite]"),
    ).toBeTruthy();
  });

  it("names what each row will do in a word, and for an update the columns it changes", async () => {
    await openWith({ state: mixed });
    const table = screen.getByRole("table", { name: "Rows found in items-september.xlsx" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Row",
      "Action",
      "Existing record",
      "SKU",
      "Name",
      "Price",
      "Quantity",
    ]);
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(9);
    // The row that needs fixing is first, with its reason.
    const first = within(rows[1] as HTMLElement);
    expect(first.getByText("Needs fixing")).toBeTruthy();
    expect(first.getByText("More than one record has that SKU, so the row cannot say which to update.")).toBeTruthy();

    const update = within(rows[2] as HTMLElement);
    expect(update.getByText("Update")).toBeTruthy();
    expect(update.getByText("Changes: Price")).toBeTruthy();
    expect(within(rows[4] as HTMLElement).getByText("Changes: Name, Price")).toBeTruthy();
    expect(within(rows[5] as HTMLElement).getByText("New")).toBeTruthy();
    expect(within(rows[7] as HTMLElement).getByText("No change")).toBeTruthy();
  });

  it("names the stored record each row will change, so a shifted Record ID column is seen before anything is sent", async () => {
    await openWith({ state: mixed });
    const table = screen.getByRole("table", { name: "Rows found in items-september.xlsx" });
    const rows = within(table).getAllByRole("row");
    // The zinc bracket's row says which record it will change: the stored name may differ from the sheet's.
    expect(within(rows[4] as HTMLElement).getByText("Bracket, zinc plated (BRK-120)")).toBeTruthy();
    expect(within(rows[2] as HTMLElement).getByText("Steel bracket (BRK-100)")).toBeTruthy();
    // A row that names no record has an empty cell, said to a screen reader too.
    const created = within(rows[5] as HTMLElement);
    expect(created.getByText("New")).toBeTruthy();
    expect(created.getAllByText("Empty").length).toBeGreaterThan(0);
  });

  it("has no Existing record column when adding only: no row names a record", async () => {
    await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
    const table = screen.getByRole("table", { name: "Rows found in companies-september.xlsx" });
    expect(within(table).queryByRole("columnheader", { name: "Existing record" })).toBeNull();
  });

  it("explains a row that matches but changes nothing when it is not what it looks like", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_NO_BASELINE }) });
    expect(
      screen.getByText(
        "The SKU differs from the record's only in capitals, and a row found by it cannot change that, so nothing was sent. Use the Item ID to change only the capitals of a name.",
      ),
    ).toBeTruthy();
    // A row that plainly changes nothing keeps just the word: only the row with a note has more.
    const table = screen.getByRole("table", { name: "Rows found in prices-copied-from-june.xlsx" });
    expect(within(table).getAllByText("No change")).toHaveLength(1);
    expect(within(table).getAllByText("Update")).toHaveLength(2);
  });

  it("says what each row will do on the button, not just how many rows there are", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_ROWS_WITHOUT_ID }) });
    expect(screen.getByRole("button", { name: "Update 1, add 2" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Import \d+ rows?$/ })).toBeNull();
  });

  it("counts the rows it will send, new and updated together, on the button", async () => {
    await openWith({ state: mixed });
    expect((screen.getByRole("button", { name: "Update 3, add 2" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("cannot be started when every row already matches, and says so instead of 'Import 0 rows'", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_ALL_UNCHANGED }) });
    const button = screen.getByRole("button", { name: "Nothing to change" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /^Import \d+ rows?$/ })).toBeNull();
    expect(screen.getByText("Nothing to change.")).toBeTruthy();
    expect(screen.getByText("Nothing to change: every row already matches its record.")).toBeTruthy();
    expect(figure("No change")).toBe("4");
  });

  it("shows why a row was refused, for an unknown Record ID and for a cell that cannot be changed", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_REFUSALS }) });
    expect(
      screen.getByText(
        "That Item ID is not in this list: the record may have been deleted, the file came from another organisation, or it is outside the records you can see.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("SKU cannot be changed once the record exists (it is 'BRK-100'). Leave the cell as it is, or blank."),
    ).toBeTruthy();
    expect(screen.getByText("Name cannot be cleared")).toBeTruthy();
    expect(screen.getByText("This row updates the same record as row 2. Keep only one of the two rows.")).toBeTruthy();
    expect(screen.getAllByText("Needs fixing")).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Update 1" })).toBeTruthy();
  });

  it("warns above the rows when the file has nothing that names a record", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_CANNOT_MATCH }) });
    expect(
      screen.getByText(
        "This file has no Item ID or SKU column, so it cannot update existing records; every row would be added as new.",
      ),
    ).toBeTruthy();
    expect(screen.getAllByText("SKU is required to add a new record")).toHaveLength(3);
  });

  it("does not warn when the file can name records", async () => {
    await openWith({ state: mixed });
    expect(screen.queryByText(/cannot update existing records/)).toBeNull();
  });

  it("counts, above the rows, those that have no id and will be added as new records", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_ROWS_WITHOUT_ID }) });
    expect(screen.getByText("2 rows have no Item ID, so they will be created as new records.")).toBeTruthy();
  });

  describe("says how the cells of the file are compared with the records", () => {
    it("with a baseline: only the cells changed since the export are used", async () => {
      await openWith({ state: mixed });
      expect(screen.getByText("Only the cells you changed since the export are used.")).toBeTruthy();
      expect(screen.getByText("Anything a colleague changed since then stays as they left it.")).toBeTruthy();
      expect(screen.queryByText(/no export baseline/)).toBeNull();
    });

    it("without one: every filled cell is compared with the record as it is now, so an old cell puts its old value back", async () => {
      await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_NO_BASELINE }) });
      expect(
        screen.getByText("This file has no export baseline, so every filled cell is compared with the record as it is now."),
      ).toBeTruthy();
      expect(screen.getByText("A cell copied from an old export puts its old value back. Export again if unsure.")).toBeTruthy();
      expect(screen.queryByText(/Only the cells you changed/)).toBeNull();
    });

    it("with one that could not be used: says it was ignored, and warns the same", async () => {
      await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_UNUSABLE_BASELINE }) });
      expect(screen.getByText(/The export baseline in this file is damaged or belongs to another list/)).toBeTruthy();
      expect(screen.getByText("A cell copied from an old export puts its old value back. Export again if unsure.")).toBeTruthy();
    });

    it("says nothing when adding, or when no row names a record", async () => {
      await openWith({ state: stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }) });
      expect(screen.queryByText(/export baseline/)).toBeNull();
      expect(screen.queryByText(/Only the cells you changed/)).toBeNull();
    });
  });

  it("says why a preview was refreshed instead of an import started, above the file's name", async () => {
    const notice =
      "The records changed while you were reviewing this file, so the preview has been refreshed and nothing was imported. Check it, then choose Import again.";
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_MIXED, notice }) });
    const alert = screen.getByText(notice);
    expect(alert.closest("[role=status]")).toBeTruthy();
    expect(alert.compareDocumentPosition(screen.getByText("items-september.xlsx"))).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("shows no notice on a preview that was not refreshed", async () => {
    await openWith({ state: mixed });
    expect(screen.queryByText(/nothing was imported/)).toBeNull();
  });

  it("starts the import once, from the keyboard", async () => {
    const { user, actions } = await openWith({ state: mixed });
    screen.getByRole("button", { name: "Update 3, add 2" }).focus();
    await user.keyboard("{Enter}");
    expect(actions.startImport).toHaveBeenCalledTimes(1);
  });
});

describe("importing with updates", () => {
  const running = updating({ phase: "importing", progress: PROGRESS_UPDATING });

  it("counts what was created, what was updated and what failed, and has no skipped rows", async () => {
    await openWith({ state: running });
    expect(figure("Created")).toBe("6");
    expect(figure("Updated")).toBe("38");
    expect(figure("Failed")).toBe("2");
    expect(figure("Skipped, already exist")).toBeUndefined();
    expect(screen.getByText("46 of 120 rows sent")).toBeTruthy();
  });

  it("says that rows already updated stay updated if the import is cancelled", async () => {
    await openWith({ state: running });
    expect(screen.getByText(/Rows already created or updated stay that way if you cancel/)).toBeTruthy();
  });

  it("keeps the counts of an add-only import as they were", async () => {
    await openWith({ state: stateFor({ phase: "importing", progress: { ...PROGRESS_UPDATING, updated: 0, skipped: 4 } }) });
    expect(figure("Skipped, already exist")).toBe("4");
    expect(figure("Updated")).toBeUndefined();
  });
});

describe("the result of an update", () => {
  const done = updating({ phase: "done", summary: SUMMARY_UPDATE_WITH_FAILURES });

  it("says what became of the rows in one sentence, and counts each kind", async () => {
    await openWith({ state: done });
    expect(screen.getByRole("heading", { name: "Import finished" })).toBeTruthy();
    expect(screen.getByText("3 updated, 2 created, 5 unchanged, 1 failed.")).toBeTruthy();
    for (const [label, value] of [
      ["Created", "2"],
      ["Updated", "3"],
      ["Unchanged", "5"],
      ["Failed", "1"],
      ["Invalid", "0"],
    ] as const) {
      expect(figure(label)).toBe(value);
    }
    expect(figure("Skipped, already exist")).toBeUndefined();
  });

  it("lists only the rows that need a look, with the server's sentence", async () => {
    await openWith({ state: done });
    expect(screen.getByText("1 row was not imported. The result report lists every row.")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Rows that were not imported" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(within(rows[1] as HTMLElement).getByText("BRK-150")).toBeTruthy();
    expect(within(rows[1] as HTMLElement).getByText("Failed")).toBeTruthy();
    expect(within(rows[1] as HTMLElement).getByText("An item named 'Steel bracket, white' already exists.")).toBeTruthy();
    // Rows that were updated or already matched are counted, not listed.
    expect(within(table).queryByText("BRK-100")).toBeNull();
    expect(within(table).queryByText("No change")).toBeNull();
  });

  it("names the stored record a listed row was matched to, beside the row's own item", async () => {
    await openWith({ state: done });
    const table = screen.getByRole("table", { name: "Rows that were not imported" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Row",
      "Item",
      "Existing record",
      "Result",
      "Details",
    ]);
    const row = within(within(table).getAllByRole("row")[1] as HTMLElement);
    expect(row.getByText("BRK-150")).toBeTruthy();
    expect(row.getByText("Bracket, nickel (BRK-150)")).toBeTruthy();
  });

  it("says which columns were saved and which were not for a row that failed part-way", async () => {
    await openWith({ state: updating({ phase: "done", summary: SUMMARY_UPDATE_PARTLY_SAVED }) });
    expect(
      screen.getByText(
        "The request could not be completed. Check your connection and try again. Saved: Name, Price. Not saved: Stage.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Steel bracket, white (BRK-110)")).toBeTruthy();
  });

  it("has no Existing record column when no listed row names a record", async () => {
    await openWith({ state: stateFor({ phase: "done", summary: SUMMARY_WITH_PROBLEMS }) });
    const table = screen.getByRole("table", { name: "Rows that were not created" });
    expect(within(table).queryByRole("columnheader", { name: "Existing record" })).toBeNull();
  });

  it("announces the counts once, and not the sentence that is on the page", async () => {
    await openWith({ state: done });
    expect(document.querySelector("[aria-live=polite]")?.textContent).toBe(
      "2 created, 3 updated, 5 unchanged, 1 failed, 0 invalid.",
    );
  });

  it("says everything imported, with no table, when nothing failed", async () => {
    await openWith({ state: updating({ phase: "done", summary: SUMMARY_UPDATE_CLEAN }) });
    expect(screen.getByText("Everything imported. 3 updated, 2 created, 5 unchanged.")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("keeps the wording of an add-only run", async () => {
    await openWith({
      state: stateFor({
        phase: "done",
        summary: { ...SUMMARY_UPDATE_WITH_FAILURES, updated: 0, unchanged: 0, skipped: 1, failed: 1, created: 2 },
      }),
    });
    expect(figure("Skipped, already exist")).toBe("1");
    expect(screen.getByRole("table", { name: "Rows that were not created" })).toBeTruthy();
  });
});
