import { useRef, useState, useCallback, useEffect } from "react";
import { MapContainer, TileLayer, ImageOverlay, useMap } from "react-leaflet";
import { LatLngBoundsExpression } from "leaflet";
import L from "leaflet";
import { ComparisonVisualization, MapVisualization } from "@/types";
import { cn, authUrl } from "@/lib/utils";
import { useAuth } from "@/components/auth/useAuth";
import { GripVertical } from "lucide-react";

interface ComparisonMapViewerProps {
  data: ComparisonVisualization;
  className?: string;
}

const BASEMAP_URL = "/tile-proxy/satellite/{z}/{y}/{x}";
const BASEMAP_ATTR = "&copy; Esri";

// Convert bounds from various formats to Leaflet format
function convertBounds(b: unknown): LatLngBoundsExpression | undefined {
  if (!b) return undefined;
  if (Array.isArray(b) && b.length === 2 && Array.isArray(b[0])) return b as LatLngBoundsExpression;
  if (Array.isArray(b) && b.length === 4 && typeof b[0] === "number") {
    const [west, south, east, north] = b as [number, number, number, number];
    return [
      [south, west],
      [north, east],
    ];
  }
  if (typeof b === "object" && b !== null && "west" in b) {
    const bbox = b as {
      west: number;
      south: number;
      east: number;
      north: number;
    };
    return [
      [bbox.south, bbox.west],
      [bbox.north, bbox.east],
    ];
  }
  return undefined;
}

// Sync one map's view to match another
function SyncView({ sourceMap }: { sourceMap: L.Map | null }) {
  const map = useMap();

  useEffect(() => {
    if (!sourceMap) return;

    const syncTarget = () => {
      map.setView(sourceMap.getCenter(), sourceMap.getZoom(), {
        animate: false,
      });
    };

    sourceMap.on("move", syncTarget);
    sourceMap.on("zoom", syncTarget);

    return () => {
      sourceMap.off("move", syncTarget);
      sourceMap.off("zoom", syncTarget);
    };
  }, [map, sourceMap]);

  return null;
}

// Fix map sizing
function MapResizer() {
  const map = useMap();
  useEffect(() => {
    const timer = setTimeout(() => map.invalidateSize(), 150);
    const observer = new ResizeObserver(() => map.invalidateSize());
    const container = map.getContainer();
    if (container) observer.observe(container);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [map]);
  return null;
}

function FitBounds({ bounds }: { bounds: LatLngBoundsExpression }) {
  const map = useMap();
  useEffect(() => {
    if (bounds) map.fitBounds(bounds, { padding: [20, 20] });
  }, [map, bounds]);
  return null;
}

function MapSide({
  data,
  label,
  mapRef,
  syncMap,
  isPrimary,
}: {
  data: MapVisualization;
  label: string;
  mapRef: React.MutableRefObject<L.Map | null>;
  syncMap: L.Map | null;
  isPrimary: boolean;
}) {
  const bounds = convertBounds(data.bounds);
  // Overlay tiles load via <img>-style requests that cannot carry an
  // Authorization header, so the token must ride the URL (authUrl).
  const { accessToken: authToken } = useAuth();

  return (
    <div className="relative h-full w-full">
      <MapContainer
        center={bounds ? undefined : [20, 0]}
        zoom={bounds ? undefined : 2}
        bounds={bounds}
        style={{ height: "100%", width: "100%" }}
        zoomControl={false}
        ref={mapRef}
        dragging={isPrimary}
        scrollWheelZoom={isPrimary}
        doubleClickZoom={isPrimary}
        touchZoom={isPrimary}
        boxZoom={isPrimary}
        keyboard={isPrimary}
      >
        <MapResizer />
        <TileLayer url={BASEMAP_URL} attribution={BASEMAP_ATTR} />
        {bounds && isPrimary && <FitBounds bounds={bounds} />}
        {!isPrimary && syncMap && <SyncView sourceMap={syncMap} />}

        {data.type === "raster" && data.url && bounds && (
          <ImageOverlay
            url={authUrl(data.url, authToken)}
            bounds={bounds}
            opacity={data.opacity ?? 0.7}
          />
        )}
      </MapContainer>

      {/* Label badge */}
      <div className="absolute left-3 top-3 z-[1000] rounded-md bg-black/70 px-3 py-1 text-xs font-medium text-white backdrop-blur-sm">
        {label}
      </div>
    </div>
  );
}

export function ComparisonMapViewer({ data, className }: ComparisonMapViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const leftMapRef = useRef<L.Map | null>(null);
  const rightMapRef = useRef<L.Map | null>(null);
  const [splitPosition, setSplitPosition] = useState(50); // percentage
  const [isDragging, setIsDragging] = useState(false);
  const [leftMapReady, setLeftMapReady] = useState<L.Map | null>(null);

  const beforeLabel = data.labels?.before || "Before";
  const afterLabel = data.labels?.after || "After";

  // Track when left map is ready for syncing
  useEffect(() => {
    const check = setInterval(() => {
      if (leftMapRef.current) {
        setLeftMapReady(leftMapRef.current);
        clearInterval(check);
      }
    }, 100);
    return () => clearInterval(check);
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isDragging || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const pct = Math.max(15, Math.min(85, (x / rect.width) * 100));
      setSplitPosition(pct);
    },
    [isDragging]
  );

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  // Invalidate map sizes when split changes
  useEffect(() => {
    const timer = setTimeout(() => {
      leftMapRef.current?.invalidateSize();
      rightMapRef.current?.invalidateSize();
    }, 50);
    return () => clearTimeout(timer);
  }, [splitPosition]);

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative flex h-full w-full select-none overflow-hidden rounded-lg",
        isDragging && "cursor-col-resize",
        className
      )}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Left (Before) */}
      <div className="h-full overflow-hidden" style={{ width: `${splitPosition}%` }}>
        <MapSide
          data={data.before}
          label={beforeLabel}
          mapRef={leftMapRef}
          syncMap={null}
          isPrimary={true}
        />
      </div>

      {/* Draggable divider */}
      <div
        className={cn(
          "absolute top-0 z-[1001] flex h-full w-6 -translate-x-1/2 cursor-col-resize items-center justify-center",
          isDragging && "bg-primary/10"
        )}
        style={{ left: `${splitPosition}%` }}
        onMouseDown={handleMouseDown}
      >
        {/* Visible handle */}
        <div
          className={cn(
            "flex h-12 w-6 items-center justify-center rounded-full border-2 bg-white shadow-lg transition-colors",
            isDragging ? "border-primary" : "border-gray-300 hover:border-primary"
          )}
        >
          <GripVertical size={14} className="text-gray-500" />
        </div>
        {/* Vertical line */}
        <div className="absolute top-0 h-full w-0.5 bg-white/80" />
      </div>

      {/* Right (After) */}
      <div className="h-full overflow-hidden" style={{ width: `${100 - splitPosition}%` }}>
        <MapSide
          data={data.after}
          label={afterLabel}
          mapRef={rightMapRef}
          syncMap={leftMapReady}
          isPrimary={false}
        />
      </div>
    </div>
  );
}
