import type { PlanVersionOut } from "@/api/catalog";
import { KIND_LABEL, priceLabel } from "./planFormat";

function featureValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}

/** One version's full sheet: prices, features and seat classes, read-only. */
export function VersionSheet({ version }: { version: PlanVersionOut }) {
  const priceIndex = new Map(version.prices.map((p, index) => [p.id, index + 1]));
  return (
    <div className="flex flex-col gap-6">
      <section aria-label={`Prices of version ${version.version_no}`} className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold text-ink">Prices</h4>
        {version.prices.length === 0 ? (
          <p className="text-sm text-muted">No prices — this draft cannot be published until it has one.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th scope="col" className="py-1 pr-4 font-medium">#</th>
                <th scope="col" className="py-1 pr-4 font-medium">Kind</th>
                <th scope="col" className="py-1 font-medium">Price</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {version.prices.map((price, index) => (
                <tr key={price.id}>
                  <td className="tnum py-2 pr-4 text-muted">{index + 1}</td>
                  <td className="py-2 pr-4">{KIND_LABEL[price.kind]}</td>
                  <td className="mono py-2">{priceLabel(price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section aria-label={`Features of version ${version.version_no}`} className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold text-ink">Features</h4>
        {version.features.length === 0 ? (
          <p className="text-sm text-muted">None declared. A trial uses the platform default of 14 days.</p>
        ) : (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {version.features.map((feature) => (
              <div key={feature.feature_key} className="flex flex-col gap-1">
                <dt className="mono text-xs text-muted">{feature.feature_key}</dt>
                <dd className="break-words text-ink">{featureValue(feature.value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      {version.seat_classes.length > 0 ? (
        <section aria-label={`Seat classes of version ${version.version_no}`} className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold text-ink">Seat classes</h4>
          <ul className="flex flex-col gap-1 text-sm">
            {version.seat_classes.map((seat) => (
              <li key={seat.code} className="flex flex-wrap items-baseline gap-2">
                <span className="font-medium text-ink">{seat.name}</span>
                <span className="text-muted">
                  {seat.price_id === null
                    ? "Free"
                    : `Charged at price #${priceIndex.get(seat.price_id) ?? "?"}`}
                  {seat.can_write ? "" : " · view-only"}
                  {seat.included_count !== null ? ` · ${seat.included_count} included` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
