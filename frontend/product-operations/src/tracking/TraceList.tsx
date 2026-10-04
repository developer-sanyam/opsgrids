import { Link } from "react-router-dom";

import type { TraceNodeOut } from "@/api/trackingTypes";

interface TraceListProps {
  /** The heading above the list: "Came from", "Became". */
  title: string;
  nodes: TraceNodeOut[];
  /** Which screen a node opens. */
  kind: "lot" | "roll";
  /** Said when the trace reached nothing, so an empty list is an answer and not a blank. */
  emptyText: string;
}

function describe(node: TraceNodeOut): string {
  const how = node.relation ? node.relation.toLowerCase().replace(/_/g, " ") : null;
  const parts = [how, node.depth > 1 ? `${node.depth} steps away` : null].filter(Boolean);
  return parts.join(" · ");
}

/**
 * One direction of a trace: the lots or rolls reached, nearest first, each a link. A trace is a
 * tree flattened into a list with `from_id`, so the line says how a node was reached and how far
 * away it is rather than pretending to draw a graph.
 */
export function TraceList({ title, nodes, kind, emptyText }: TraceListProps) {
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-ink-soft">{title}</h3>
      {nodes.length === 0 ? (
        <p className="text-sm text-muted">{emptyText}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {nodes.map((node) => (
            <li key={`${node.from_id}-${node.id}`} className="flex flex-wrap items-baseline gap-x-3 text-sm">
              <Link
                to={`/inventory/${kind}s/${node.id}`}
                className="tnum font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                {node.number}
              </Link>
              {describe(node) ? <span className="text-muted">{describe(node)}</span> : null}
              {kind === "roll" && node.lot_no ? <span className="text-muted">lot {node.lot_no}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
