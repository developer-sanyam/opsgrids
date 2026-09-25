import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { Card } from "./Card";

/**
 * The composed loading states the brief asks for (§12–14: dashboard, table, card, form, chart),
 * built from one pulsing block so they all breathe together.
 *
 * Each preset is one `role="status"` region, `aria-busy`, that says what is loading in words —
 * the blocks themselves are hidden from assistive technology, because "image, image, image" is
 * not information. Size them like the content they stand in for, so nothing moves when it lands.
 * Motion is Tailwind's `animate-pulse`; the harness and reduced-motion users see it still.
 */

/**
 * One placeholder block. On a card it is the ground colour; on the page's own ground (a page
 * header) that would be invisible, so there it takes the hairline colour instead.
 */
function Block({ className, on = "card" }: { className: string; on?: "card" | "page" }) {
  return <div aria-hidden className={cn("animate-pulse", on === "card" ? "bg-ground" : "bg-line", className)} />;
}

function Loading({ label, className, children }: { label: string; className?: string | undefined; children: ReactNode }) {
  return (
    <div role="status" aria-busy="true" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

/** Heights for a bar chart's placeholder bars: varied, so it reads as a chart and not a comb. */
const BAR_HEIGHTS = ["h-2/5", "h-3/5", "h-1/2", "h-4/5", "h-2/3", "h-3/4", "h-1/2", "h-5/6"];

function CardBlocks({ lines }: { lines: number }) {
  return (
    <div className="flex flex-col gap-3">
      <Block className="h-5 w-1/3 rounded-full" />
      {Array.from({ length: lines }, (_, index) => (
        <Block key={index} className={cn("h-3 rounded-full", index === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

function ChartBlocks({ variant }: { variant: "area" | "bar" | "donut" }) {
  if (variant === "donut") {
    return (
      <div className="flex items-center gap-6">
        <Block className="size-32 shrink-0 rounded-full" />
        <div className="flex flex-1 flex-col gap-3">
          {Array.from({ length: 4 }, (_, index) => (
            <Block key={index} className="h-3 w-full rounded-full" />
          ))}
        </div>
      </div>
    );
  }
  if (variant === "bar") {
    return (
      <div className="flex h-48 items-end gap-3">
        {BAR_HEIGHTS.map((height, index) => (
          <Block key={index} className={cn("flex-1 rounded-t-tile", height)} />
        ))}
      </div>
    );
  }
  return <Block className="h-48 w-full rounded-tile" />;
}

function KpiBlocks() {
  return (
    <Card className="flex flex-col gap-4 p-5">
      <Block className="size-10 rounded-tile" />
      <Block className="h-3 w-1/2 rounded-full" />
      <Block className="h-7 w-2/3 rounded-full" />
    </Card>
  );
}

function TableBlocks({ rows, columns }: { rows: number; columns: number }) {
  return (
    <div className="flex flex-col">
      <div className="flex gap-4 border-b border-line py-3">
        {Array.from({ length: columns }, (_, index) => (
          <Block key={index} className="h-3 flex-1 rounded-full" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex gap-4 border-b border-line py-4 last:border-0">
          {Array.from({ length: columns }, (_, index) => (
            <Block key={index} className={cn("h-4 flex-1 rounded-full", index > 0 && "opacity-70")} />
          ))}
        </div>
      ))}
    </div>
  );
}

interface CardSkeletonProps {
  /** Text lines under the title bar. Default 3. */
  lines?: number | undefined;
  /** What a screen reader hears: "Loading recent activity". */
  label?: string | undefined;
  className?: string | undefined;
}

/** A card-shaped placeholder: a title and a few lines, on the card's own surface. */
export function CardSkeleton({ lines = 3, label = "Loading", className }: CardSkeletonProps) {
  return (
    <Loading label={label} className={className}>
      <Card className="p-5">
        <CardBlocks lines={lines} />
      </Card>
    </Loading>
  );
}

interface TableSkeletonProps {
  rows?: number | undefined;
  columns?: number | undefined;
  label?: string | undefined;
  className?: string | undefined;
}

/**
 * A table-shaped placeholder, for a list screen before its first page arrives. Place it where the
 * table goes (inside its card). `DataTable`'s own `loading` is the better answer once the columns
 * are known — it keeps the real header.
 */
export function TableSkeleton({ rows = 5, columns = 4, label = "Loading", className }: TableSkeletonProps) {
  return (
    <Loading label={label} className={className}>
      <TableBlocks rows={rows} columns={columns} />
    </Loading>
  );
}

interface FormSkeletonProps {
  /** Fields to draw. Default 4. */
  fields?: number | undefined;
  /** Two columns from 768px, like a `FormSection`. Default 1. */
  columns?: 1 | 2 | undefined;
  label?: string | undefined;
  className?: string | undefined;
}

/** An edit form waiting for the record it edits: label and control per field, then the actions. */
export function FormSkeleton({ fields = 4, columns = 1, label = "Loading", className }: FormSkeletonProps) {
  return (
    <Loading label={label} className={cn("flex flex-col gap-6", className)}>
      <div className={cn("grid grid-cols-1 gap-4", columns === 2 && "md:grid-cols-2")}>
        {Array.from({ length: fields }, (_, index) => (
          <div key={index} className="flex flex-col gap-2">
            <Block className="h-3 w-24 rounded-full" />
            <Block className="h-11 w-full rounded-tile" />
          </div>
        ))}
      </div>
      <div className="flex justify-end gap-2">
        <Block className="h-11 w-24 rounded-full" />
        <Block className="h-11 w-32 rounded-full" />
      </div>
    </Loading>
  );
}

interface ChartSkeletonProps {
  /** The shape of the chart that is coming. Default `area`. */
  variant?: "area" | "bar" | "donut" | undefined;
  label?: string | undefined;
  className?: string | undefined;
}

/** A chart's plot area while its series loads. `ChartCard`'s `loading` draws this inside the card. */
export function ChartSkeleton({ variant = "area", label = "Loading", className }: ChartSkeletonProps) {
  return (
    <Loading label={label} className={className}>
      <ChartBlocks variant={variant} />
    </Loading>
  );
}

interface DashboardSkeletonProps {
  label?: string | undefined;
  className?: string | undefined;
}

/**
 * A whole dashboard in the brief's order (§5): header, a row of four KPIs, a large chart beside a
 * secondary one, then a table. For the first paint of a home screen, before any of its requests
 * have answered; once some have, let each card show its own state instead.
 */
export function DashboardSkeleton({ label = "Loading dashboard", className }: DashboardSkeletonProps) {
  return (
    <Loading label={label} className={cn("flex flex-col gap-6", className)}>
      <div className="flex flex-col gap-2">
        <Block on="page" className="h-7 w-56 rounded-full" />
        <Block on="page" className="h-3 w-80 max-w-full rounded-full" />
      </div>
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <KpiBlocks key={index} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="flex flex-col gap-4 p-5 xl:col-span-2">
          <Block className="h-5 w-40 rounded-full" />
          <ChartBlocks variant="area" />
        </Card>
        <Card className="flex flex-col gap-4 p-5">
          <Block className="h-5 w-32 rounded-full" />
          <ChartBlocks variant="donut" />
        </Card>
      </div>
      <Card className="p-5">
        <TableBlocks rows={4} columns={4} />
      </Card>
    </Loading>
  );
}
