import { useState, useEffect, useCallback, useRef } from "react";
import { Play, Pause, SkipBack, SkipForward } from "lucide-react";
import { cn } from "@/lib/utils";

interface TemporalLayer {
  id: string;
  timestamp: string;
  title?: string;
}

interface TimelineSliderProps {
  layers: TemporalLayer[];
  activeLayerId: string;
  onLayerChange: (layerId: string) => void;
  className?: string;
}

/**
 * Inline timeline scrubber for temporal map layers.
 * Renders below the map when multiple temporal layers are present.
 * Provides play/pause, step forward/back, speed control, and a
 * draggable slider with date labels.
 */
export function TimelineSlider({
  layers,
  activeLayerId,
  onLayerChange,
  className,
}: TimelineSliderProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1000);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const currentIndex = layers.findIndex((l) => l.id === activeLayerId);
  const safeIndex = currentIndex >= 0 ? currentIndex : 0;

  // Auto-play
  useEffect(() => {
    if (isPlaying) {
      intervalRef.current = setInterval(() => {
        const nextIdx = layers.findIndex((l) => l.id === activeLayerId) + 1;
        if (nextIdx >= layers.length) {
          setIsPlaying(false);
        } else {
          onLayerChange(layers[nextIdx]!.id);
        }
      }, speed);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlaying, speed, activeLayerId, layers, onLayerChange]);

  const handlePlay = useCallback(() => {
    if (safeIndex >= layers.length - 1) {
      // At end — restart from beginning
      onLayerChange(layers[0]!.id);
    }
    setIsPlaying(true);
  }, [safeIndex, layers, onLayerChange]);

  const handlePause = useCallback(() => setIsPlaying(false), []);

  const handlePrev = useCallback(() => {
    setIsPlaying(false);
    if (safeIndex > 0) onLayerChange(layers[safeIndex - 1]!.id);
  }, [safeIndex, layers, onLayerChange]);

  const handleNext = useCallback(() => {
    setIsPlaying(false);
    if (safeIndex < layers.length - 1) onLayerChange(layers[safeIndex + 1]!.id);
  }, [safeIndex, layers, onLayerChange]);

  const handleSliderChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setIsPlaying(false);
      const idx = Number(e.target.value);
      if (layers[idx]) onLayerChange(layers[idx].id);
    },
    [layers, onLayerChange],
  );

  // Format timestamp for display
  const formatDate = (ts: string) => {
    try {
      const d = new Date(ts);
      return d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return ts;
    }
  };

  if (layers.length < 2) return null;

  const currentLayer = layers[safeIndex];
  const firstDate = formatDate(layers[0]!.timestamp);
  const lastDate = formatDate(layers[layers.length - 1]!.timestamp);

  return (
    <div
      className={cn(
        "flex items-center gap-3 border-t border-border bg-card/95 px-4 py-2 backdrop-blur-sm",
        className,
      )}
    >
      {/* Play controls */}
      <div className="flex items-center gap-0.5">
        <button
          onClick={handlePrev}
          disabled={safeIndex === 0}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
          aria-label="Previous time step"
        >
          <SkipBack className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={isPlaying ? handlePause : handlePlay}
          className="rounded p-1.5 text-primary hover:bg-primary/10"
          aria-label={isPlaying ? "Pause timeline" : "Play timeline"}
        >
          {isPlaying ? (
            <Pause className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
        </button>
        <button
          onClick={handleNext}
          disabled={safeIndex >= layers.length - 1}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
          aria-label="Next time step"
        >
          <SkipForward className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Start date */}
      <span className="hidden text-[10px] text-muted-foreground sm:inline">
        {firstDate}
      </span>

      {/* Slider */}
      <div className="relative flex-1">
        <input
          type="range"
          min={0}
          max={layers.length - 1}
          value={safeIndex}
          onChange={handleSliderChange}
          className="w-full cursor-pointer accent-primary"
          aria-label="Timeline position"
        />
      </div>

      {/* End date */}
      <span className="hidden text-[10px] text-muted-foreground sm:inline">
        {lastDate}
      </span>

      {/* Current date badge */}
      <span className="min-w-[5rem] rounded bg-primary/10 px-2 py-0.5 text-center text-xs font-medium text-primary">
        {formatDate(currentLayer?.timestamp || "")}
      </span>

      {/* Speed control */}
      <select
        value={speed}
        onChange={(e) => setSpeed(Number(e.target.value))}
        className="rounded border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground"
        aria-label="Playback speed"
      >
        <option value={2000}>0.5x</option>
        <option value={1000}>1x</option>
        <option value={500}>2x</option>
        <option value={250}>4x</option>
      </select>

      {/* Frame counter */}
      <span className="text-[10px] tabular-nums text-muted-foreground">
        {safeIndex + 1}/{layers.length}
      </span>
    </div>
  );
}
