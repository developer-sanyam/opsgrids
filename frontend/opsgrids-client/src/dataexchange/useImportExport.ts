import { useMemo } from "react";

import type {
  DataExchangeActions,
  DataExchangeState,
  ImportEntity,
  UseImportExportOptions,
  UseImportExportResult,
} from "./types";

const IDLE: DataExchangeState = {
  phase: "menu",
  error: null,
  preview: null,
  progress: null,
  summary: null,
  downloadingTemplate: false,
  exporting: null,
  notice: null,
};

/**
 * PLACEHOLDER (ADR-0022): idle state, every action a no-op. The engine slice replaces this file
 * with the real hook; the signature is the contract and does not change.
 */
export function useImportExport<Payload, Item = unknown, Filters = void>(
  entity: ImportEntity<Payload, Item, Filters>,
  // The real hook reads the screen's filters for the export; the placeholder ignores them.
  options?: UseImportExportOptions<Filters>,
): UseImportExportResult {
  void options;
  return useMemo(() => {
    const noop = async (): Promise<void> => undefined;
    const actions: DataExchangeActions = {
      openFile: noop,
      startImport: noop,
      cancelImport: () => undefined,
      reset: () => undefined,
      downloadTemplate: noop,
      exportAll: noop,
      cancelExport: () => undefined,
      downloadReport: noop,
    };
    return {
      state: IDLE,
      actions,
      meta: {
        noun: entity.noun,
        nounSingular: entity.nounSingular,
        maxRows: entity.maxRows ?? 1000,
        exportMaxRows: entity.export?.maxRows ?? 10_000,
        requiredHeaders: entity.columns
          .filter((c) => c.required === true && c.readOnly !== true)
          .map((c) => c.header),
        canExport: entity.export !== undefined,
      },
    };
  }, [entity]);
}
