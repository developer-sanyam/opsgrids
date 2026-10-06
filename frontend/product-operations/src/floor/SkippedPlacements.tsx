import type { PublishedPlacementOut } from "@/api/types";
import { Card, SectionHeader } from "@/ui";

/**
 * The locations a published layout names but that cannot be drawn — a zero or non-numeric size, an
 * impossible position. Listed rather than dropped, so nobody concludes a bin is missing from the
 * warehouse because it is missing from the picture.
 */
export function SkippedPlacements({ placements }: { placements: readonly PublishedPlacementOut[] }) {
  if (placements.length === 0) return null;
  return (
    <Card className="flex flex-col gap-3 p-4">
      <SectionHeader
        title="Not on the floor plan"
        subtitle="These locations are on the published layout but have no usable position or size, so they can't be drawn."
      />
      <ul className="flex flex-wrap gap-2">
        {placements.map((p) => (
          <li key={p.location_id} className="rounded-chip bg-sunken px-2 py-1 text-sm text-ink-soft">
            <span className="tnum font-semibold text-ink">{p.code}</span> · {p.name}
          </li>
        ))}
      </ul>
    </Card>
  );
}
