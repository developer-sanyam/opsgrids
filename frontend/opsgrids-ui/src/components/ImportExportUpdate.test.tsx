/**
 * Update mode in the Import / Export dialog: the choice between adding only and adding and
 * updating, what a preview says about a row that changes a record, and what an update run reports.
 * The state is the caller's (a data-exchange hook), so these tests hold it in props and change it by
 * hand, the way the hook would.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { UPDATE_RULES } from "../lib/importExportCopy";
import type { ImportExportMeta, ImportExportViewActions, ImportExportViewState } from "../lib/importExportView";
import {
  META,
  PREVIEW_UPDATE_ALL_UNCHANGED,
  PREVIEW_UPDATE_CANNOT_MATCH,
  PREVIEW_UPDATE_MIXED,
  PREVIEW_UPDATE_REFUSALS,
  PREVIEW_WITH_ERRORS,
  PROGRESS_UPDATING,
  SUMMARY_UPDATE_CLEAN,
  SUMMARY_UPDATE_WITH_FAILURES,
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
    expect(screen.getByRole("radio", { name: "Add new and update existing" })).toHaveProperty("checked", true);
    const rules = within(screen.getByRole("list", { name: "How updating works" }));
    for (const rule of UPDATE_RULES) expect(rules.getByText(rule)).toBeTruthy();
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

  it("counts the rows it will send, new and updated together, on the button", async () => {
    await openWith({ state: mixed });
    expect((screen.getByRole("button", { name: "Import 5 rows" }) as HTMLButtonElement).disabled).toBe(false);
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
        "That Record ID is not in this list: the record may have been deleted, or the file came from another organisation.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("SKU cannot be changed once the record exists (it is 'BRK-100'). Leave the cell as it is, or blank."),
    ).toBeTruthy();
    expect(screen.getByText("Name cannot be cleared")).toBeTruthy();
    expect(screen.getByText("This row updates the same record as row 2. Keep only one of the two rows.")).toBeTruthy();
    expect(screen.getAllByText("Needs fixing")).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Import 1 row" })).toBeTruthy();
  });

  it("warns above the rows when the file has nothing that names a record", async () => {
    await openWith({ state: updating({ phase: "preview", preview: PREVIEW_UPDATE_CANNOT_MATCH }) });
    expect(
      screen.getByText(
        "This file has no Record ID or SKU column, so it cannot update existing records; every row would be added as new.",
      ),
    ).toBeTruthy();
    expect(screen.getAllByText("SKU is required to add a new record")).toHaveLength(3);
  });

  it("does not warn when the file can name records", async () => {
    await openWith({ state: mixed });
    expect(screen.queryByText(/cannot update existing records/)).toBeNull();
  });

  it("starts the import once, from the keyboard", async () => {
    const { user, actions } = await openWith({ state: mixed });
    screen.getByRole("button", { name: "Import 5 rows" }).focus();
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
