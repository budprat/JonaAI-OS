import React from "react";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
}

export const Sparkline: React.FC<SparklineProps> = ({
  data,
  width = 120,
  height = 32,
  className,
}) => {
  if (!data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");

  const lastVal = data[data.length - 1]!;
  const firstVal = data[0]!;
  const trending = lastVal >= firstVal;

  return (
    <svg width={width} height={height} className={className} viewBox={`0 0 ${width} ${height}`}>
      <polyline
        points={points}
        fill="none"
        stroke={trending ? "hsl(var(--status-good))" : "hsl(var(--status-critical))"}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};
