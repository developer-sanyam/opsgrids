import { useId, useState } from "react";
import type { KeyboardEvent } from "react";

import { formatMinor } from "@/lib/format";
import { useLineList } from "@/store/lineList";
import { T, step } from "./tokens";

/**
 * The basket in the announcement bar: a button that opens what is in it.
 *
 * It used to be text, because there is no basket *page* and a control that looks like a
 * destination and goes nowhere costs a buyer more than it is worth. It is a button now because it
 * does go somewhere — down, to the lines the buyer has added and what they total — and a thing a
 * buyer taps to check their basket is a button on every shop they have used. It opens a disclosure
 * in place; it navigates nowhere, and there is still no checkout behind it.
 *
 * The count sits in an `aria-live` region so ADD is announced; the disclosure is tied to the button
 * by `aria-expanded` and `aria-controls`, and Escape closes it.
 */
export function CartButton({
  label,
  count,
  noun,
}: {
  /** "My cart" or "Line list" — the mode's own word. */
  label: string;
  count: number;
  /** "item" or "line", singular. */
  noun: string;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const list = useLineList();
  const entries = Array.from(list.lines.values());
  const countText = `${count} ${count === 1 ? noun : `${noun}s`}`;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && open) {
      event.stopPropagation();
      setOpen(false);
    }
  }

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- an Escape barrier for the disclosure, not a control: the button and the controls inside it are the operable parts.
    <div style={{ position: "relative" }} onKeyDown={onKeyDown}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        data-st-action=""
        onClick={() => setOpen((previous) => !previous)}
        style={{
          display: "inline-flex",
          alignItems: "baseline",
          gap: step(0.3),
          fontFamily: T.fontBody,
          fontSize: "0.8125rem",
          fontWeight: 700,
          color: T.text,
          whiteSpace: "nowrap",
          background: "transparent",
          border: `1px solid ${T.border}`,
          borderRadius: T.radius,
          padding: `${step(0.45)} ${step(0.7)}`,
          cursor: "pointer",
        }}
      >
        <span>{label}</span>
        <span
          aria-live="polite"
          style={{ color: T.muted, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
        >
          {countText}
        </span>
      </button>

      <div
        id={panelId}
        role="region"
        aria-label={label}
        hidden={!open}
        style={{
          position: "absolute",
          right: 0,
          top: "100%",
          marginTop: step(0.35),
          zIndex: 5,
          width: `min(${step(18)}, 80vw)`,
          background: T.bg,
          color: T.text,
          border: `1px solid ${T.border}`,
          borderRadius: T.radius,
          padding: step(0.8),
          fontFamily: T.fontBody,
          fontSize: "0.8125rem",
        }}
      >
        {entries.length === 0 ? (
          <p style={{ margin: 0, color: T.muted }}>Nothing here yet. Add a line from the catalogue.</p>
        ) : (
          <>
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: step(0.5) }}>
              {entries.map((entry) => (
                <li
                  key={entry.key}
                  style={{ display: "flex", justifyContent: "space-between", gap: step(0.6), alignItems: "baseline" }}
                >
                  <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                    {entry.name} × {entry.qty}
                  </span>
                  <span style={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                    {formatMinor(entry.unitPriceMinor * entry.qty, entry.currency)}
                  </span>
                </li>
              ))}
            </ul>
            <p
              style={{
                margin: 0,
                marginTop: step(0.6),
                paddingTop: step(0.5),
                borderTop: `1px solid ${T.border}`,
                display: "flex",
                justifyContent: "space-between",
                fontWeight: 700,
              }}
            >
              <span>Total</span>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{formatMinor(list.totalMinor)}</span>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
