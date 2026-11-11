import { useState } from "react";
import { Info, ChevronUp } from "lucide-react";

/** Mirrors RasterStats from @/types — kept inline to avoid path-alias issues with tsc CLI */
interface RasterStats {
  min: number;
  max: number;
  mean: number;
  std: number;
  median?: number;
  nodata_percent: number;
  valid_pixels?: number;
  total_pixels?: number;
  crs?: string;
  resolution?: [number, number];
  width?: number;
  height?: number;
  bands?: number;
  dtype?: string;
  file_size_bytes?: number;
}

/** Mirrors the subset of ResultTypeConfig used here */
interface ResultTypeConfig {
  label: string;
  formatValue: (v: number) => string;
}

interface StatsInfoPanelProps {
  stats: RasterStats;
  resultConfig: ResultTypeConfig;
}

function formatFileSize(bytes?: number): string {
  if (!bytes) return "\u2014";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatResolution(res?: [number, number]): string {
  if (!res) return "\u2014";
  // Convert degrees to approximate meters if values are small (likely geographic CRS)
  if (Math.abs(res[0]) < 1) {
    const meters = Math.round(res[0] * 111320);
    return `~${meters}m`;
  }
  return `${res[0].toFixed(1)} x ${res[1].toFixed(1)}`;
}

export function StatsInfoPanel({ stats, resultConfig }: StatsInfoPanelProps) {
  const [expanded, setExpanded] = useState(false);

  if (!expanded) {
    return (
      <button
        onClick={(e) => {
          e.stopPropagation();
          setExpanded(true);
        }}
        title="Raster statistics"
        className="flex h-8 w-8 items-center justify-center rounded-md border border-black/10 bg-white shadow-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <Info size={15} />
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-white/20 bg-black/70 px-3 py-2 backdrop-blur-sm text-white min-w-[200px]">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-semibold text-white/90">
          {resultConfig.label} Statistics
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setExpanded(false);
          }}
          className="text-white/60 hover:text-white/90 transition-colors"
        >
          <ChevronUp size={14} />
        </button>
      </div>

      {/* Value statistics */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
        <div>
          <span className="text-white/50">Min:</span>{" "}
          <span className="tabular-nums text-white/90">
            {resultConfig.formatValue(stats.min ?? 0)}
          </span>
        </div>
        <div>
          <span className="text-white/50">Max:</span>{" "}
          <span className="tabular-nums text-white/90">
            {resultConfig.formatValue(stats.max ?? 0)}
          </span>
        </div>
        <div>
          <span className="text-white/50">Mean:</span>{" "}
          <span className="tabular-nums text-white/90">
            {resultConfig.formatValue(stats.mean ?? 0)}
          </span>
        </div>
        <div>
          <span className="text-white/50">Std:</span>{" "}
          <span className="tabular-nums text-white/90">
            {resultConfig.formatValue(stats.std ?? 0)}
          </span>
        </div>
        <div>
          <span className="text-white/50">NoData:</span>{" "}
          <span className="tabular-nums text-white/90">
            {(stats.nodata_percent ?? 0).toFixed(1)}%
          </span>
        </div>
      </div>

      {/* Metadata */}
      {(stats.crs || stats.width || stats.resolution) && (
        <div className="mt-1.5 border-t border-white/10 pt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
          {stats.crs && (
            <div className="col-span-2">
              <span className="text-white/50">CRS:</span>{" "}
              <span className="text-white/90">{stats.crs}</span>
            </div>
          )}
          {stats.resolution && (
            <div>
              <span className="text-white/50">Res:</span>{" "}
              <span className="tabular-nums text-white/90">
                {formatResolution(stats.resolution)}
              </span>
            </div>
          )}
          {stats.width && stats.height && (
            <div>
              <span className="text-white/50">Size:</span>{" "}
              <span className="tabular-nums text-white/90">
                {stats.width}x{stats.height}
              </span>
            </div>
          )}
          {stats.bands && (
            <div>
              <span className="text-white/50">Bands:</span>{" "}
              <span className="tabular-nums text-white/90">{stats.bands}</span>
            </div>
          )}
          {stats.file_size_bytes && (
            <div>
              <span className="text-white/50">File:</span>{" "}
              <span className="tabular-nums text-white/90">
                {formatFileSize(stats.file_size_bytes)}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
