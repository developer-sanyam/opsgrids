import { LOCATION_KIND_COLOR } from "@/data/locationKindColor";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import type { LocationKind } from "@/api/types";
import { formatNumber } from "@/lib/format";
import { Card } from "@/ui";
import { cn } from "@opsgrids/ui";

interface SceneLegendProps {
  kinds: LocationKind[];
  count: number;
  /** Where the boxes came from, so the legend never overstates it: "the published floor plan" or
   *  "the location tree". */
  source: string;
  highlightedCount: number;
}

/** What the colours in the 3-D view mean, and what the view is built from. */
export function SceneLegend({ kinds, count, source, highlightedCount }: SceneLegendProps) {
  return (
    <Card className="flex w-full flex-col gap-3 p-4 lg:w-64">
      <div className="flex flex-col gap-1">
        <p className="text-sm font-bold text-ink">Legend</p>
        <p className="text-xs text-muted">
          {formatNumber(count)} location{count === 1 ? "" : "s"} shown from {source}, coloured by kind.
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {kinds.map((kind) => (
          <li key={kind} className="flex items-center gap-2 text-sm">
            <span className={cn("size-3 shrink-0 rounded-chip", LOCATION_KIND_COLOR[kind].swatchClass)} aria-hidden />
            <span className="text-ink-soft">{LOCATION_KIND_LABEL[kind]}</span>
          </li>
        ))}
      </ul>
      {highlightedCount > 0 ? (
        <p className="text-xs text-ink">
          Boxes your search found are outlined and labelled “Found”, and drawn in the accent colour.
        </p>
      ) : null}
      <p className="text-xs text-faint">Click a box to see that location and what is stored in it.</p>
    </Card>
  );
}
