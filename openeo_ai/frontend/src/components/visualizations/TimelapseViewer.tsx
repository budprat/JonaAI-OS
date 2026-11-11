import { useState, useEffect, useCallback, useRef } from "react";
import { MapContainer, TileLayer, ImageOverlay } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import { Play, Pause, SkipBack, SkipForward } from "lucide-react";
import type { TimelapseVisualization } from "@/types";
import { cn, authUrl } from "@/lib/utils";
import { useAuth } from "@/components/auth/useAuth";
import { ColorLegend } from "./map-tools/ColorLegend";
import { detectResultType } from "@/hooks/useResultType";

interface TimelapseViewerProps {
  data: TimelapseVisualization;
  className?: string;
  title?: string;
}

export function TimelapseViewer({ data, className, title }: TimelapseViewerProps) {
  const { layers, colormap } = data;
  // Overlay tiles load via <img>-style requests that cannot carry an
  // Authorization header, so the token must ride the URL (authUrl).
  const { accessToken: authToken } = useAuth();
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1000); // ms between frames
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const currentLayer = layers[currentIndex];

  // Auto-play logic
  useEffect(() => {
    if (isPlaying) {
      intervalRef.current = setInterval(() => {
        setCurrentIndex((prev) => {
          if (prev >= layers.length - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, speed);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlaying, speed, layers.length]);

  const handlePlay = useCallback(() => {
    if (currentIndex >= layers.length - 1) {
      setCurrentIndex(0);
    }
    setIsPlaying(true);
  }, [currentIndex, layers.length]);

  const handlePause = useCallback(() => {
    setIsPlaying(false);
  }, []);

  const handlePrev = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex((prev) => Math.max(0, prev - 1));
  }, []);

  const handleNext = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex((prev) => Math.min(layers.length - 1, prev + 1));
  }, [layers.length]);

  if (layers.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        No temporal layers to display
      </div>
    );
  }

  // Compute center from first layer bounds
  const bounds: LatLngBoundsExpression | undefined = currentLayer?.bounds
    ? [currentLayer.bounds[0], currentLayer.bounds[1]]
    : undefined;

  const center: [number, number] = bounds
    ? [
        (currentLayer!.bounds![0]![0] + currentLayer!.bounds![1]![0]) / 2,
        (currentLayer!.bounds![0]![1] + currentLayer!.bounds![1]![1]) / 2,
      ]
    : [20, 0];

  const resultConfig = detectResultType(undefined, title || currentLayer?.timestamp);

  return (
    <div className={cn("flex h-full flex-col", className)}>
      {/* Map */}
      <div className="relative flex-1">
        <MapContainer center={center} zoom={10} className="h-full w-full" zoomControl={true}>
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="&copy; OSM"
          />
          {currentLayer?.url && bounds && (
            <ImageOverlay
              url={authUrl(currentLayer.url, authToken)}
              bounds={bounds}
              opacity={0.8}
            />
          )}
        </MapContainer>

        {/* Timestamp badge */}
        <div className="absolute left-3 top-3 z-[1000]">
          <span className="rounded bg-black/80 px-3 py-1.5 text-sm font-medium text-white">
            {currentLayer?.timestamp || `Frame ${currentIndex + 1}`}
          </span>
        </div>

        {/* Stats overlay */}
        {currentLayer?.stats && (
          <div className="absolute right-3 top-3 z-[1000] rounded bg-black/70 px-2 py-1 text-xs text-white">
            <div>Min: {currentLayer.stats.min?.toFixed(3)}</div>
            <div>Max: {currentLayer.stats.max?.toFixed(3)}</div>
            <div>Mean: {currentLayer.stats.mean?.toFixed(3)}</div>
          </div>
        )}

        {/* Color legend */}
        {colormap && currentLayer?.stats && (
          <div className="absolute bottom-3 left-3 z-[1000]">
            <ColorLegend
              colormap={colormap}
              vmin={currentLayer.stats.min ?? 0}
              vmax={currentLayer.stats.max ?? 1}
              resultConfig={resultConfig}
            />
          </div>
        )}
      </div>

      {/* Temporal controls */}
      <div className="flex items-center gap-3 border-t bg-card px-4 py-2">
        {/* Play controls */}
        <div className="flex items-center gap-1">
          <button
            onClick={handlePrev}
            disabled={currentIndex === 0}
            className="rounded p-1 hover:bg-muted disabled:opacity-30"
            aria-label="Previous frame"
          >
            <SkipBack className="h-4 w-4" />
          </button>
          <button
            onClick={isPlaying ? handlePause : handlePlay}
            className="rounded p-1.5 hover:bg-muted"
            aria-label={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </button>
          <button
            onClick={handleNext}
            disabled={currentIndex >= layers.length - 1}
            className="rounded p-1 hover:bg-muted disabled:opacity-30"
            aria-label="Next frame"
          >
            <SkipForward className="h-4 w-4" />
          </button>
        </div>

        {/* Slider */}
        <input
          type="range"
          min={0}
          max={layers.length - 1}
          value={currentIndex}
          onChange={(e) => {
            setIsPlaying(false);
            setCurrentIndex(Number(e.target.value));
          }}
          className="flex-1"
        />

        {/* Frame counter */}
        <span className="min-w-[4rem] text-right text-xs text-muted-foreground">
          {currentIndex + 1} / {layers.length}
        </span>

        {/* Speed control */}
        <select
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="rounded border bg-background px-1 py-0.5 text-xs"
        >
          <option value={2000}>0.5x</option>
          <option value={1000}>1x</option>
          <option value={500}>2x</option>
          <option value={250}>4x</option>
        </select>
      </div>
    </div>
  );
}
