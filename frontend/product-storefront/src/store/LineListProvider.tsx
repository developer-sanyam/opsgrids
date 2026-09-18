/**
 * The one component that fills `LineListContext`.
 *
 * Everything it does is in `lineList.ts` — the reducer, the keys, the totals — so the rules are
 * exercised as arithmetic and this file holds nothing but the wiring. `StoreRenderer` is the only
 * place it is mounted; anywhere else, `useLineList()` reaches the no-op default instead and a card
 * behaves exactly as it did before the list existed.
 */

import { useCallback, useMemo, useReducer } from "react";
import type { ReactNode } from "react";

import {
  EMPTY_LINE_LIST,
  LineListContext,
  lineCount,
  lineListReducer,
  lineTotalMinor,
  lineUnits,
  type LineDraft,
  type LineListApi,
} from "./lineList";

export function LineListProvider({ children }: { children: ReactNode }) {
  const [lines, dispatch] = useReducer(lineListReducer, EMPTY_LINE_LIST);

  const add = useCallback((line: LineDraft, qty: number) => dispatch({ type: "add", line, qty }), []);
  const setQty = useCallback((key: string, qty: number) => dispatch({ type: "set-qty", key, qty }), []);
  const remove = useCallback((key: string) => dispatch({ type: "remove", key }), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);

  /* One identity per state, so a press that changed nothing re-renders nothing: the reducer returns
     the same map when it has no work to do, and this memo carries that through to consumers. */
  const value = useMemo<LineListApi>(
    () => ({
      lines,
      count: lineCount(lines),
      units: lineUnits(lines),
      totalMinor: lineTotalMinor(lines),
      qtyOf: (key: string) => lines.get(key)?.qty ?? 0,
      add,
      setQty,
      remove,
      clear,
    }),
    [lines, add, setQty, remove, clear],
  );

  return <LineListContext.Provider value={value}>{children}</LineListContext.Provider>;
}
