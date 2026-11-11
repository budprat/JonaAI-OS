import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

/** Mirrors the subset of ResultTypeConfig used here */
interface ResultTypeConfig {
  label: string;
  unit: string;
  formatValue: (v: number) => string;
  legendLabels?: { low: string; high: string };
}

/** Mirrors RasterStats from @/types — kept inline to avoid path-alias issues with tsc CLI */
interface RasterStats {
  min: number;
  max: number;
  mean: number;
  std: number;
  nodata_percent: number;
}

const COLORMAP_GRADIENTS: Record<string, string> = {
  viridis:
    "linear-gradient(to right, #440154, #482777, #3e4989, #31688e, #26838f, #1f9d8a, #6cce5a, #b6de2b, #fee825)",
  ndvi: "linear-gradient(to right, #a50026, #d73027, #f46d43, #fdae61, #fee08b, #ffffbf, #d9ef8b, #a6d96a, #66bd63, #1a9850, #006837)",
  coolwarm:
    "linear-gradient(to right, #3b4cc0, #6788ee, #9abbff, #c9d7ef, #edd1c2, #f7a889, #e26952, #b40426)",
  plasma:
    "linear-gradient(to right, #0d0887, #5b02a3, #9a179b, #cb4679, #eb7852, #fbb32f, #f0f921)",
  inferno:
    "linear-gradient(to right, #000004, #1b0c41, #4a0c6b, #781c6d, #a52c60, #cf4446, #ed6925, #fb9b06, #f7d13d, #fcffa4)",
  terrain:
    "linear-gradient(to right, #006147, #107a2f, #e8d77d, #a14300, #821e1e, #a1a1a1, #cecece, #ffffff)",
  magma:
    "linear-gradient(to right, #000004, #180f3d, #440f76, #721f81, #9e2f7f, #cd4071, #f1605d, #fd9668, #feca8d, #fcfdbf)",
  cividis:
    "linear-gradient(to right, #00204d, #00416c, #345e70, #65726b, #8e8960, #b59b4a, #dbb42c, #fdd225)",
  rdylgn:
    "linear-gradient(to right, #a50026, #d73027, #f46d43, #fdae61, #fee08b, #ffffbf, #d9ef8b, #a6d96a, #66bd63, #1a9850, #006837)",
  rdylbu:
    "linear-gradient(to right, #a50026, #d73027, #f46d43, #fdae61, #fee090, #ffffbf, #e0f3f8, #abd9e9, #74add1, #4575b4, #313695)",
  spectral:
    "linear-gradient(to right, #9e0142, #d53e4f, #f46d43, #fdae61, #fee08b, #ffffbf, #e6f598, #abdda4, #66c2a5, #3288bd, #5e4fa2)",
  rdbu: "linear-gradient(to right, #67001f, #b2182b, #d6604d, #f4a582, #fddbc7, #f7f7f7, #d1e5f0, #92c5de, #4393c3, #2166ac, #053061)",
  ylgnbu:
    "linear-gradient(to right, #ffffd9, #edf8b1, #c7e9b4, #7fcdbb, #41b6c4, #1d91c0, #225ea8, #253494, #081d58)",
  grayscale: "linear-gradient(to right, #000000, #333333, #666666, #999999, #cccccc, #ffffff)",
  hot: "linear-gradient(to right, #000000, #e50000, #ff8c00, #ffff00, #ffffff)",
  ylorrd:
    "linear-gradient(to right, #ffffcc, #ffeda0, #fed976, #feb24c, #fd8d3c, #fc4e2a, #e31a1c, #b10026)",
  ylorbr:
    "linear-gradient(to right, #ffffe5, #fff7bc, #fee391, #fec44f, #fe9929, #ec7014, #cc4c02, #8c2d04)",
  set2: "linear-gradient(to right, #66c2a5, #fc8d62, #8da0cb, #e78ac3, #a6d854, #ffd92f, #e5c494, #b3b3b3)",
};

function getGradient(colormap: string): string {
  const key = colormap.toLowerCase().replace(/[_-]/g, "");
  return COLORMAP_GRADIENTS[key] ?? COLORMAP_GRADIENTS["viridis"] ?? "";
}

interface ColorLegendProps {
  vmin: number;
  vmax: number;
  colormap: string;
  resultConfig: ResultTypeConfig;
  stats?: RasterStats;
}

export function ColorLegend({ vmin, vmax, colormap, resultConfig, stats }: ColorLegendProps) {
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) {
    return (
      <button
        onClick={(e) => {
          e.stopPropagation();
          setCollapsed(false);
        }}
        className="flex items-center gap-1 rounded-lg border border-white/20 bg-black/70 px-2 py-1 text-[9px] text-white/70 backdrop-blur-sm transition-colors hover:text-white/90"
      >
        <ChevronUp size={10} />
        {resultConfig.label}
      </button>
    );
  }

  const range = vmax - vmin;
  const tickValues = [0.25, 0.5, 0.75].map((p) => vmin + p * range);
  const meanPercent =
    stats?.mean !== undefined && range > 0 ? ((stats.mean - vmin) / range) * 100 : null;

  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-white/20 bg-black/70 px-2.5 py-1.5 backdrop-blur-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-medium text-white/90">{resultConfig.label}</span>
        <div className="flex items-center gap-1">
          {resultConfig.unit && (
            <span className="text-[9px] text-white/60">({resultConfig.unit})</span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setCollapsed(true);
            }}
            className="text-white/40 transition-colors hover:text-white/70"
          >
            <ChevronDown size={10} />
          </button>
        </div>
      </div>

      {/* Gradient bar with mean indicator */}
      <div className="relative">
        <div className="h-3 w-44 rounded-sm" style={{ background: getGradient(colormap) }} />
        {/* Mean indicator triangle */}
        {meanPercent !== null && meanPercent >= 0 && meanPercent <= 100 && (
          <div
            className="absolute -bottom-1"
            style={{ left: `${meanPercent}%`, transform: "translateX(-50%)" }}
            title={`Mean: ${resultConfig.formatValue(stats!.mean)}`}
          >
            <div
              className="h-0 w-0"
              style={{
                borderLeft: "3px solid transparent",
                borderRight: "3px solid transparent",
                borderBottom: "4px solid rgba(255,255,255,0.8)",
              }}
            />
          </div>
        )}
      </div>

      {/* Tick marks */}
      <div className="relative h-1.5 w-44">
        {[0.25, 0.5, 0.75].map((p) => (
          <div
            key={p}
            className="absolute top-0 h-1.5 w-px bg-white/40"
            style={{ left: `${p * 100}%` }}
          />
        ))}
      </div>

      {/* Min/Max + tick values */}
      <div className="flex justify-between">
        <span className="text-[9px] tabular-nums text-white/80">
          {resultConfig.formatValue(vmin)}
        </span>
        {tickValues.map((v, i) => (
          <span key={i} className="text-[8px] tabular-nums text-white/50">
            {resultConfig.formatValue(v)}
          </span>
        ))}
        <span className="text-[9px] tabular-nums text-white/80">
          {resultConfig.formatValue(vmax)}
        </span>
      </div>

      {resultConfig.legendLabels && (
        <div className="flex justify-between">
          <span className="text-[8px] text-white/50">{resultConfig.legendLabels.low}</span>
          <span className="text-[8px] text-white/50">{resultConfig.legendLabels.high}</span>
        </div>
      )}

      {/* Mean label */}
      {stats?.mean !== undefined && (
        <div className="text-center text-[8px] text-white/50">
          Mean: {resultConfig.formatValue(stats.mean)}
        </div>
      )}
    </div>
  );
}
