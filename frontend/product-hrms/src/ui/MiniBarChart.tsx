import { cn } from "@/lib/cn";

interface MiniBarChartProps {
  data: number[];
  /** Which bar to paint mint; defaults to the last (most recent) point. */
  highlightIndex?: number;
  className?: string;
  width?: number;
  height?: number;
}

/**
 * A tiny inline bar chart for stat cards — no charting library, just SVG. Decorative summary
 * of a series whose headline number is already announced, so it is aria-hidden.
 */
export function MiniBarChart({
  data,
  highlightIndex,
  className,
  width = 92,
  height = 40,
}: MiniBarChartProps) {
  const max = Math.max(...data, 1);
  const gap = 3;
  const barWidth = (width - gap * (data.length - 1)) / data.length;
  const hi = highlightIndex ?? data.length - 1;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn("overflow-visible", className)}
      aria-hidden
    >
      {data.map((v, i) => {
        const h = Math.max(3, (v / max) * height);
        const x = i * (barWidth + gap);
        const y = height - h;
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={barWidth}
            height={h}
            rx={2}
            className={i === hi ? "fill-mint-strong" : "fill-line"}
          />
        );
      })}
    </svg>
  );
}

interface SparklineProps {
  data: number[];
  className?: string;
  width?: number;
  height?: number;
}

/** A minimal mint sparkline for trend hints. Aria-hidden — the number carries the meaning. */
export function Sparkline({
  data,
  className,
  width = 92,
  height = 40,
}: SparklineProps) {
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const step = width / (data.length - 1 || 1);
  const points = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / span) * (height - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn("overflow-visible", className)}
      aria-hidden
    >
      <polyline
        points={points.join(" ")}
        fill="none"
        className="stroke-mint-strong"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
