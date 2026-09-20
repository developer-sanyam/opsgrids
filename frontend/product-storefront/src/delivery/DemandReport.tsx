import { useCallback, useState } from "react";

import { useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  InlineAlert,
  Pill,
  SearchIcon,
  SectionHeader,
  Skeleton,
  Tag,
  TextField,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { getDemand } from "./api";
import { demandOutcomeLabel, formatShortDate, isUnmetDemand } from "./copy";
import { DeliveryProblem } from "./DeliveryProblem";
import type { DemandTotal, StoreOut } from "./types";

/**
 * The expansion map.
 *
 * ADR-0010 §7: every check increments one daily counter row, and this report is "these postal
 * codes were asked for and we said no, this many times, in descending order". **It is the only
 * demand signal a merchant has before they have taken a single order**, and it is the reason
 * slice 1 pays for itself on its own.
 *
 * It is folded into `storefront.delivery.zone.read` rather than given a fifth permission (§9):
 * anyone who can see the zones can see which postal codes were turned away.
 */
export function DemandReport({ store }: { store: StoreOut }) {
  const [range, setRange] = useState(() => defaultRange());
  const [applied, setApplied] = useState(() => defaultRange());
  const [unmetOnly, setUnmetOnly] = useState(true);
  // Retrying the same range has to re-run the request, and the range strings have not changed —
  // so the attempt counter is what the effect keys on.
  const [attempt, setAttempt] = useState(0);

  // `since`/`until` are the endpoint's own parameter names. The rows come back one per postal code
  // **per day**; `getDemand` walks the pages and sums them into the range totals shown here.
  const load = useCallback(() => getDemand(applied.from, applied.to), [applied]);
  const state = useAsync(load, [applied.from, applied.to, attempt]);

  const rows =
    state.status === "success"
      ? state.data.rows.filter((row) => (unmetOnly ? isUnmetDemand(row.outcome) : true))
      : [];

  const columns: Column<DemandTotal>[] = [
    {
      key: "postal_code",
      header: "Postal code",
      cellClassName: "tnum font-bold",
      render: (row) => row.postal_code,
    },
    {
      key: "outcome",
      header: "What they were told",
      render: (row) => (
        <Tag tone={isUnmetDemand(row.outcome) ? "warning" : "neutral"}>
          {demandOutcomeLabel(row.outcome)}
        </Tag>
      ),
    },
    {
      key: "checks",
      header: "Times asked",
      align: "right",
      // The number the merchant came for. `w-px whitespace-nowrap` shrinks the column to its
      // content so the outcome column absorbs the slack instead — without it, at 390px the counts
      // were pushed past the table's right edge and read as "3", "2", "1".
      cellClassName: "tnum font-bold w-px whitespace-nowrap",
      headerClassName: "w-px",
      render: (row) => row.checks.toLocaleString("en-IN"),
    },
    {
      key: "last",
      header: "Last asked",
      align: "right",
      // Header *and* cell, together. Hiding only the header left a fourth column of dates under a
      // three-column head, which is what pushed "Times asked" off screen at 390px.
      cellClassName: "text-muted hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (row) => formatShortDate(row.last_checked_on),
    },
  ];

  const invalidRange = range.from > range.to;

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-4 p-5 sm:p-6">
        <SectionHeader
          title="Who asked, and what you told them"
          subtitle="One counted row per postal code per day. No buyer is identified, and no address is stored."
        />

        <form
          className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start"
          onSubmit={(event) => {
            event.preventDefault();
            if (!invalidRange) setApplied(range);
          }}
        >
          <TextField
            label="From"
            type="date"
            value={range.from}
            max={range.to}
            onChange={(event) => setRange((current) => ({ ...current, from: event.target.value }))}
            containerClassName="sm:max-w-48"
          />
          <TextField
            label="To"
            type="date"
            value={range.to}
            min={range.from}
            onChange={(event) => setRange((current) => ({ ...current, to: event.target.value }))}
            containerClassName="sm:max-w-48"
            {...(invalidRange ? { error: "The end date has to be on or after the start date." } : {})}
          />
          <Button type="submit" className="sm:mt-6" disabled={invalidRange}>
            Apply
          </Button>
        </form>

        <p className="text-xs text-muted">
          Dates are read in {store.timezone}, the store's own timezone — not your browser's. Counts
          are kept for 400 days.
        </p>

        <div className="flex flex-wrap items-center gap-2">
          <Pill active={unmetOnly} onClick={() => setUnmetOnly(true)}>
            Turned away
          </Pill>
          <Pill active={!unmetOnly} onClick={() => setUnmetOnly(false)}>
            Every check
          </Pill>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          /* Four rows of the table's own height, so the card does not grow when the data lands. */
          <div className="flex flex-col gap-3 p-3">
            {[0, 1, 2, 3, 4].map((row) => (
              <Skeleton key={row} className="h-10 w-full rounded-tile" />
            ))}
          </div>
        ) : null}

        {state.status === "error" ? (
          <div className="p-3">
            <DeliveryProblem
              error={state.error}
              subject="The demand report"
              onRetry={() => setAttempt((current) => current + 1)}
            />
          </div>
        ) : null}

        {/* The page cap stopped the walk, so every total below is a floor. Saying so is the whole
            difference between a report and a guess. */}
        {state.status === "success" && state.data.truncated ? (
          <div className="p-3">
            <InlineAlert tone="warning" title="This range has more counted days than one report can hold">
              The totals below cover only the first part of the range, so a postal code further down
              the list may really be the busiest. Narrow the dates to get the true order.
            </InlineAlert>
          </div>
        ) : null}

        {state.status === "success" && rows.length === 0 ? (
          <div className="py-14">
            <DemandEmpty unmetOnly={unmetOnly} store={store} />
          </div>
        ) : null}

        {state.status === "success" && rows.length > 0 ? (
          <>
            {/* Four nowrap headers come to more than 390px on their own, so below `sm` the table
                scrolled sideways and cut off "Times asked" — the one number the merchant opened
                this report for. Measured in Chrome; the same markup passed in jsdom, which has no
                layout engine. The stacked list below carries the same four facts. */}
            <div className="hidden sm:block">
              <DataTable
                columns={columns}
                rows={rows}
                rowKey={(row) => `${row.postal_code}-${row.outcome}`}
                caption="Postal codes people checked, most asked first"
              />
            </div>

            <ul className="m-0 flex list-none flex-col gap-2 p-1 sm:hidden">
              {rows.map((row) => (
                <li
                  key={`${row.postal_code}-${row.outcome}`}
                  className="flex items-center justify-between gap-3 rounded-tile border border-line p-3"
                >
                  <div className="flex min-w-0 flex-col gap-1">
                    <span className="font-bold tnum">{row.postal_code}</span>
                    <Tag tone={isUnmetDemand(row.outcome) ? "warning" : "neutral"}>
                      {demandOutcomeLabel(row.outcome)}
                    </Tag>
                  </div>
                  <div className="shrink-0 text-right">
                    <span className="block text-lg font-extrabold tnum">
                      {row.checks.toLocaleString("en-IN")}
                    </span>
                    <span className="block text-xs text-muted">
                      asked · last {formatShortDate(row.last_checked_on)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Card>
    </div>
  );
}

/**
 * An empty demand report is **not** a failure, and saying "no data" would leave a merchant
 * wondering which of five things is broken. So it names them: the check is off by default in the
 * announcement bar, a draft store answers 404 to every buyer, and a range with no traffic in it
 * counts nothing.
 */
function DemandEmpty({ unmetOnly, store }: { unmetOnly: boolean; store: StoreOut }) {
  const draft = store.status !== "published";
  return (
    <EmptyState
      icon={<SearchIcon className="size-6" />}
      title={unmetOnly ? "Nobody has been turned away in this range" : "No checks in this range"}
      description={
        draft
          ? `Your store is ${store.status}, so the public check answers 404 to every buyer and nothing is counted. Publish it and this fills up on its own.`
          : unmetOnly
            ? "Either every visitor was in a zone you already cover, or nobody has checked. Switch to “Every check” to tell those two apart, or widen the dates."
            : "Checks are counted only when a buyer uses the serviceability box, which is off by default in your announcement bar's settings. Switch it on, or widen the dates."
      }
    />
  );
}

/** The last 30 days, ending today. Written from local parts, never from a UTC ISO slice. */
function defaultRange(): { from: string; to: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 29);
  return { from: isoDate(start), to: isoDate(today) };
}

function isoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
