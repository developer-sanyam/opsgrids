import { cn } from "../lib/cn";

interface SparklineProps {
  data: number[];
  /**
   * Colour by meaning, not direction: `good` strokes green, `bad` strokes red — matching the
   * delta pill beside it. A falling cost line is `good`.
   */
  tone: "good" | "bad";
  className?: string;
  width?: number;
  height?: number;
}

/**
 * A tiny trend line for a KPI card — no charting library, just an SVG polyline. Decorative
 * summary of a series whose headline number is already announced, so it is aria-hidden.
 */
export function Sparkline({
  data,
  tone,
  className,
  width = 84,
  height = 32,
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

  const stroke = tone === "good" ? "var(--color-positive)" : "var(--color-negative)";
  const last = points[points.length - 1]?.split(",") ?? ["0", "0"];

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
        stroke={stroke}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={last[0]} cy={last[1]} r={2.4} fill={stroke} />
    </svg>
  );
}
