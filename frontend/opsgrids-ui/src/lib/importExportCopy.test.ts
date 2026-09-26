import { describe, expect, it } from "vitest";

import {
  liveMessage,
  percentOf,
  phaseTitle,
  previewSentence,
  rowsLabel,
  summarySentence,
} from "./importExportCopy";
import type { ImportExportSummary } from "./importExportView";
import {
  META,
  MENU_STATE,
  PREVIEW_MISSING_COLUMNS,
  PREVIEW_WITH_ERRORS,
  PROGRESS_RUNNING,
  SUMMARY_CANCELLED,
  SUMMARY_CLEAN,
  SUMMARY_WITH_PROBLEMS,
  stateFor,
} from "../story-import-export";

function summary(overrides: Partial<ImportExportSummary>): ImportExportSummary {
  return { created: 0, skipped: 0, failed: 0, invalid: 0, cancelled: false, outcomes: [], ...overrides };
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
