import { describe, expect, it } from "vitest";

import {
  UPDATE_RULES,
  importButtonLabel,
  liveMessage,
  percentOf,
  phaseTitle,
  previewSentence,
  readingSentence,
  rowsLabel,
  summarySentence,
} from "./importExportCopy";
import type { ImportExportSummary } from "./importExportView";
import {
  META,
  MENU_STATE,
  PREVIEW_MISSING_COLUMNS,
  PREVIEW_UPDATE_ALL_UNCHANGED,
  PREVIEW_UPDATE_MIXED,
  PREVIEW_UPDATE_REFUSALS,
  PREVIEW_WITH_ERRORS,
  PROGRESS_RUNNING,
  SUMMARY_CANCELLED,
  SUMMARY_CLEAN,
  SUMMARY_UPDATE_CLEAN,
  SUMMARY_UPDATE_WITH_FAILURES,
  SUMMARY_WITH_PROBLEMS,
  stateFor,
} from "../story-import-export";

function summary(overrides: Partial<ImportExportSummary>): ImportExportSummary {
  return {
    created: 0,
    updated: 0,
    unchanged: 0,
    skipped: 0,
    failed: 0,
    invalid: 0,
    cancelled: false,
    outcomes: [],
    ...overrides,
  };
}

describe("counting", () => {
  it("says one row and many rows", () => {
    expect(rowsLabel(0)).toBe("0 rows");
    expect(rowsLabel(1)).toBe("1 row");
    expect(rowsLabel(12)).toBe("12 rows");
  });

  it("is a whole percent that never leaves 0 to 100 and never divides by zero", () => {
    expect(percentOf(0, 120)).toBe(0);
    expect(percentOf(46, 120)).toBe(38);
    expect(percentOf(120, 120)).toBe(100);
    expect(percentOf(130, 120)).toBe(100);
    expect(percentOf(-4, 120)).toBe(0);
    expect(percentOf(5, 0)).toBe(0);
  });
});

describe("phaseTitle", () => {
  it("names every phase, and says cancelled when it was", () => {
    expect(phaseTitle(MENU_STATE, META)).toBe("Import / Export companies");
    expect(phaseTitle(stateFor({ phase: "reading" }), META)).toBe("Reading your file");
    expect(phaseTitle(stateFor({ phase: "preview" }), META)).toBe("Review your file");
    expect(phaseTitle(stateFor({ phase: "importing" }), META)).toBe("Importing companies");
    expect(phaseTitle(stateFor({ phase: "done", summary: SUMMARY_CLEAN }), META)).toBe("Import finished");
    expect(phaseTitle(stateFor({ phase: "done", summary: SUMMARY_CANCELLED }), META)).toBe("Import cancelled");
    expect(phaseTitle(stateFor({ phase: "error" }), META)).toBe("We couldn't read that file");
  });
});

describe("previewSentence", () => {
  it("says what can be imported and what cannot", () => {
    expect(previewSentence(PREVIEW_WITH_ERRORS)).toBe("5 rows ready to import, 3 need fixing.");
    expect(previewSentence({ ...PREVIEW_WITH_ERRORS, invalid: 1 })).toBe("5 rows ready to import, 1 needs fixing.");
    expect(previewSentence({ ...PREVIEW_WITH_ERRORS, invalid: 0, valid: 1 })).toBe("1 row ready to import.");
  });

  it("puts a missing column and an empty file ahead of the counts", () => {
    expect(previewSentence(PREVIEW_MISSING_COLUMNS)).toBe(
      "Your file is missing required columns, so nothing can be imported.",
    );
    expect(previewSentence({ ...PREVIEW_WITH_ERRORS, total: 0, valid: 0, invalid: 0, sample: 0 })).toBe(
      "No rows were found in this file.",
    );
  });
});

describe("summarySentence", () => {
  it("says everything imported for a clean run, and mentions rows that already existed", () => {
    expect(summarySentence(SUMMARY_CLEAN, META)).toBe("Everything imported. 6 companies were created.");
    expect(summarySentence(summary({ created: 1 }), META)).toBe("Everything imported. 1 company was created.");
    expect(summarySentence(summary({ created: 5, skipped: 3 }), META)).toBe(
      "Everything imported. 5 companies were created. 3 already existed and were skipped.",
    );
    expect(summarySentence(summary({ created: 5, skipped: 1 }), META)).toBe(
      "Everything imported. 5 companies were created. 1 already existed and was skipped.",
    );
  });

  it("does not say everything imported when something was not", () => {
    expect(summarySentence(SUMMARY_WITH_PROBLEMS, META)).toBe(
      "3 rows were not imported. 4 companies were created.",
    );
    expect(summarySentence(summary({ failed: 1 }), META)).toBe(
      "1 row was not imported. No companies were created.",
    );
  });

  it("says nothing new when every row already existed", () => {
    expect(summarySentence(summary({ skipped: 4 }), META)).toBe(
      "Nothing new to import. Every row already exists, so nothing was overwritten.",
    );
    expect(summarySentence(summary({}), META)).toBe("No rows were sent.");
  });

  it("says it was cancelled, with what was made before it stopped", () => {
    expect(summarySentence(SUMMARY_CANCELLED, META)).toBe(
      "2 companies were created before it stopped. The remaining rows were not sent.",
    );
    expect(summarySentence(summary({ cancelled: true }), META)).toBe(
      "Nothing was created. The remaining rows were not sent.",
    );
  });
});

describe("liveMessage", () => {
  const importing = (done: number, total = 120) =>
    stateFor({ phase: "importing", progress: { ...PROGRESS_RUNNING, done, total } });

  it("changes ten times in a whole import, not once per row", () => {
    const messages = new Set(Array.from({ length: 121 }, (_, done) => liveMessage(importing(done))));
    expect([...messages]).toHaveLength(11);
    expect(liveMessage(importing(0))).toBe("Import started.");
    expect(liveMessage(importing(120))).toBe("Importing: 100% of rows sent.");
  });

  it("does not put the countdown in the wait, so it stays quiet while the seconds tick", () => {
    const waiting = (seconds: number) =>
      liveMessage(
        stateFor({
          phase: "importing",
          progress: { ...PROGRESS_RUNNING, waiting: { reason: "rate-limit", seconds } },
        }),
      );
    expect(waiting(42)).toBe(waiting(41));
  });

  it("says the export in tenths, and the wait for its first page", () => {
    const exporting = (fetched: number, total: number) => liveMessage(stateFor({ exporting: { fetched, total } }));
    expect(exporting(0, 0)).toBe("Preparing the export.");
    expect(exporting(0, 2000)).toBe("Export started.");
    expect(exporting(600, 2000)).toBe("Exporting: 30% done.");
    expect(exporting(619, 2000)).toBe("Exporting: 30% done.");
  });

  it("says nothing on the error screen, whose alert speaks for itself", () => {
    expect(liveMessage(stateFor({ phase: "error", error: "That file is too large." }))).toBe("");
  });

  it("says the sample sheet is being prepared", () => {
    expect(liveMessage(stateFor({ downloadingTemplate: true }))).toBe("Preparing the sample sheet.");
  });
});

describe("previewSentence in update mode", () => {
  it("says what will be sent, of what kind, and what will not", () => {
    expect(previewSentence(PREVIEW_UPDATE_MIXED)).toBe(
      "5 rows ready to import (3 to update, 2 new), 2 unchanged, 1 needs fixing.",
    );
    expect(previewSentence({ ...PREVIEW_UPDATE_MIXED, invalid: 0, unchanged: 0 })).toBe(
      "5 rows ready to import (3 to update, 2 new).",
    );
    expect(previewSentence({ ...PREVIEW_UPDATE_MIXED, valid: 1, update: 1, create: 0, invalid: 3, unchanged: 0 })).toBe(
      "1 row ready to import (1 to update), 3 need fixing.",
    );
  });

  it("says there is nothing to change when every row already matches", () => {
    expect(previewSentence(PREVIEW_UPDATE_ALL_UNCHANGED)).toBe("Nothing to change: every row already matches its record.");
  });

  it("says nothing can be sent when the rest are unchanged or refused", () => {
    expect(previewSentence({ ...PREVIEW_UPDATE_ALL_UNCHANGED, unchanged: 2, invalid: 2 })).toBe(
      "No rows to send: 2 unchanged, 2 need fixing.",
    );
    expect(previewSentence(PREVIEW_UPDATE_REFUSALS)).toBe("1 row ready to import (1 to update), 4 need fixing.");
    expect(previewSentence({ ...PREVIEW_UPDATE_MIXED, update: 0, create: 5, unchanged: 0, invalid: 0 })).toBe(
      "5 rows ready to import (5 new).",
    );
  });

  it("falls back to the plain sentence when update mode has nothing to send and nothing that matched", () => {
    expect(previewSentence({ ...PREVIEW_UPDATE_REFUSALS, valid: 0, update: 0 })).toBe("0 rows ready to import, 4 need fixing.");
  });
});

describe("importButtonLabel", () => {
  it("counts the rows that will be sent, new and updated alike", () => {
    expect(importButtonLabel(PREVIEW_UPDATE_MIXED)).toBe("Import 5 rows");
    expect(importButtonLabel(PREVIEW_WITH_ERRORS)).toBe("Import 5 rows");
    expect(importButtonLabel({ ...PREVIEW_UPDATE_MIXED, valid: 1 })).toBe("Import 1 row");
  });

  it("says there is nothing to change in update mode with nothing to send, and keeps its old words when adding", () => {
    expect(importButtonLabel(PREVIEW_UPDATE_ALL_UNCHANGED)).toBe("Nothing to change");
    expect(importButtonLabel(PREVIEW_MISSING_COLUMNS)).toBe("Import 0 rows");
  });
});

describe("summarySentence for a run that updated records", () => {
  it("says what became of every kind of row, in counts", () => {
    expect(summarySentence(SUMMARY_UPDATE_WITH_FAILURES, META)).toBe("3 updated, 2 created, 5 unchanged, 1 failed.");
  });

  it("counts an invalid row as not imported, with the failed ones", () => {
    expect(summarySentence(summary({ updated: 2, failed: 1, invalid: 2 }), META)).toBe("2 updated, 3 failed.");
  });

  it("says everything imported for a clean run, and leaves out the counts that are zero", () => {
    expect(summarySentence(SUMMARY_UPDATE_CLEAN, META)).toBe("Everything imported. 3 updated, 2 created, 5 unchanged.");
    expect(summarySentence(summary({ updated: 4 }), META)).toBe("Everything imported. 4 updated.");
  });

  it("says nothing changed when every row already matched", () => {
    expect(summarySentence(summary({ unchanged: 6 }), META)).toBe(
      "Nothing to change. Every row already matches its record, so nothing was sent.",
    );
  });

  it("says what was saved before it was stopped", () => {
    expect(summarySentence(summary({ cancelled: true, updated: 3, created: 1, unchanged: 4 }), META)).toBe(
      "3 updated, 1 created before it stopped. The remaining rows were not sent.",
    );
    expect(summarySentence(summary({ cancelled: true, unchanged: 4 }), META)).toBe(
      "Nothing was changed. The remaining rows were not sent.",
    );
  });

  it("leaves an add-only run's sentences as they were", () => {
    expect(summarySentence(SUMMARY_CLEAN, META)).toBe("Everything imported. 6 companies were created.");
    expect(summarySentence(SUMMARY_WITH_PROBLEMS, META)).toBe("3 rows were not imported. 4 companies were created.");
  });
});

describe("the words of update mode", () => {
  it("announces the counts of an update run, with no skipped rows since updating never skips", () => {
    expect(liveMessage(stateFor({ phase: "done", mode: "addAndUpdate", summary: SUMMARY_UPDATE_WITH_FAILURES }))).toBe(
      "2 created, 3 updated, 5 unchanged, 1 failed, 0 invalid.",
    );
    expect(liveMessage(stateFor({ phase: "done", summary: SUMMARY_CLEAN }))).toBe(
      "6 created, 0 already existed, 0 failed, 0 invalid.",
    );
  });

  it("says what the reading phase is doing in each mode", () => {
    expect(readingSentence("add")).toBe("Checking the columns and every row. A large file takes a few seconds.");
    expect(readingSentence("addAndUpdate")).toBe(
      "Loading the current records and comparing every row with them. A large list takes a few seconds.",
    );
  });

  it("states the rules of updating in plain words", () => {
    const text = UPDATE_RULES.join(" ");
    for (const phrase of [
      "Record ID",
      "unique key",
      "A blank cell leaves the field as it is",
      "[clear]",
      "Only fields that changed are sent",
      "cannot detect edits made by someone else",
      "Records you cannot see are not matched and will be added as new; adding one that already exists will fail as a duplicate.",
    ]) {
      expect(text, phrase).toContain(phrase);
    }
  });
});
