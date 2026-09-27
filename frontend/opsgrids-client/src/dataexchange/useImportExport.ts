import { useEffect, useMemo, useRef, useState } from "react";

import { recordIdHeader } from "./cells";
import type { AnyEntity } from "./cells";
import { IDLE_STATE, createExchange } from "./exchange";
import type { Exchange } from "./exchange";
import type {
  DataExchangeMeta,
  DataExchangeState,
  ImportEntity,
  UseImportExportOptions,
  UseImportExportResult,
} from "./types";
import { DEFAULT_EXPORT_MAX_ROWS, DEFAULT_MAX_ROWS } from "./workbook";

/**
 * The Import / Export dialog's brain (ADR-0022): read a workbook, preview it, send the valid rows
 * one at a time, report the result, and download the sample sheet or the whole filtered list.
 * `@opsgrids/ui`'s `ImportExportButton` renders what it returns.
 *
 * A list whose entity declares an `update` spec (`meta.canUpdate`) can also be updated from a sheet:
 * `actions.setMode("addAndUpdate")` makes a row that names an existing record change it, and on a
 * preview it checks the same file again in the new mode. `state.mode` is `add` until chosen and
 * goes back to `add` whenever the dialog is reset.
 *
 * The entity and the filters are read through a ref, so `actions` keeps one identity for the
 * life of the component and an entity that is redefined per render does not restart anything.
 * Everything in flight is aborted on unmount. Failures arrive as `state.error` (a file that
 * cannot be imported) or `state.notice` (a template or export that failed); nothing is thrown at
 * the caller.
 */
export function useImportExport<Payload, Item = unknown, Filters = void>(
  entity: ImportEntity<Payload, Item, Filters>,
  options?: UseImportExportOptions<Filters>,
): UseImportExportResult {
  const [state, setState] = useState<DataExchangeState>(IDLE_STATE);

  const latest = useRef<{
    entity: AnyEntity;
    filters: unknown;
    onImported: UseImportExportOptions<Filters>["onImported"];
  }>({ entity, filters: options?.filters, onImported: options?.onImported });
  useEffect(() => {
    latest.current = { entity, filters: options?.filters, onImported: options?.onImported };
  });

  const exchangeRef = useRef<Exchange | null>(null);
  if (exchangeRef.current === null) {
    exchangeRef.current = createExchange({
      entity: () => latest.current.entity,
      filters: () => latest.current.filters,
      publish: setState,
      imported: (summary) => latest.current.onImported?.(summary),
    });
  }
  const exchange = exchangeRef.current;

  useEffect(() => {
    exchange.attach();
    return () => {
      exchange.dispose();
    };
  }, [exchange]);

  // Closing the tab mid-import would strand the rest of the rows; ask first, and only then.
  const importing = state.phase === "importing";
  useEffect(() => {
    if (!importing) return undefined;
    const warn = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [importing]);

  const meta = useMemo<DataExchangeMeta>(
    () => ({
      noun: entity.noun,
      nounSingular: entity.nounSingular,
      maxRows: entity.maxRows ?? DEFAULT_MAX_ROWS,
      exportMaxRows: entity.export?.maxRows ?? DEFAULT_EXPORT_MAX_ROWS,
      requiredHeaders: entity.columns
        .filter((column) => column.required === true && column.readOnly !== true)
        .map((column) => column.header),
      canExport: entity.export !== undefined,
      canUpdate: entity.update !== undefined,
      recordIdHeader: recordIdHeader(entity),
    }),
    [entity],
  );

  return useMemo(() => ({ state, actions: exchange.actions, meta }), [state, exchange, meta]);
}
