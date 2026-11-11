import React, { useEffect, useRef, useState } from "react";
import L from "leaflet";

type LayerType = "truecolor" | "ndvi" | "moisture" | "thermal";

interface EvidenceMapProps {
  center?: L.LatLngExpression;
  zoom?: number;
  activeLayer: LayerType;
  onMouseMove?: (lat: number, lng: number, ndvi: number, moisture: number) => void;
}

const FIELD_BOUNDARY: L.LatLngExpression[] = [
  [36.785, -119.432],
  [36.785, -119.408],
  [36.774, -119.408],
  [36.774, -119.432],
];

export const EvidenceMap: React.FC<EvidenceMapProps> = ({
  center = [36.78, -119.42],
  zoom = 14,
  activeLayer,
  onMouseMove,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const overlaysRef = useRef<Record<string, L.LayerGroup>>({});
  const [loading, setLoading] = useState(true);

  // Initialize map
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: false,
    }).setView(center, zoom);

    const tileLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19 }
    ).addTo(map);

    tileLayerRef.current = tileLayer;
    mapRef.current = map;

    // Field boundary
    L.polygon(FIELD_BOUNDARY, {
      color: "#FFFFFF",
      fillColor: "transparent",
      weight: 2,
      dashArray: "6,4",
    }).addTo(map);

    // NDVI overlays
    overlaysRef.current.ndvi = L.layerGroup([
      L.polygon(
        [
          [36.783, -119.415],
          [36.783, -119.41],
          [36.776, -119.41],
          [36.776, -119.415],
        ],
        { color: "#DC2626", fillColor: "#DC2626", fillOpacity: 0.3, weight: 1 }
      ),
      L.polygon(
        [
          [36.78, -119.425],
          [36.78, -119.42],
          [36.778, -119.42],
          [36.778, -119.425],
        ],
        {
          color: "#D97706",
          fillColor: "#D97706",
          fillOpacity: 0.25,
          weight: 1,
        }
      ),
      L.polygon(
        [
          [36.782, -119.43],
          [36.782, -119.426],
          [36.776, -119.426],
          [36.776, -119.43],
        ],
        { color: "#059669", fillColor: "#059669", fillOpacity: 0.2, weight: 1 }
      ),
    ]);

    // Moisture overlays
    overlaysRef.current.moisture = L.layerGroup([
      L.polygon(
        [
          [36.783, -119.418],
          [36.783, -119.41],
          [36.776, -119.41],
          [36.776, -119.418],
        ],
        {
          color: "#2563EB",
          fillColor: "#2563EB",
          fillOpacity: 0.25,
          weight: 1,
        }
      ),
    ]);

    // Thermal overlays
    overlaysRef.current.thermal = L.layerGroup([
      L.polygon(
        [
          [36.783, -119.416],
          [36.783, -119.409],
          [36.775, -119.409],
          [36.775, -119.416],
        ],
        { color: "#DC2626", fillColor: "#EF4444", fillOpacity: 0.2, weight: 1 }
      ),
    ]);

    // Pixel inspector on mousemove
    if (onMouseMove) {
      map.on("mousemove", (e: L.LeafletMouseEvent) => {
        const ndvi = 0.3 + Math.abs(Math.sin(e.latlng.lat * 100)) * 0.5;
        const moisture = 20 + Math.abs(Math.cos(e.latlng.lng * 80)) * 40;
        onMouseMove(e.latlng.lat, e.latlng.lng, ndvi, moisture);
      });
    }

    setTimeout(() => {
      map.invalidateSize();
      setLoading(false);
    }, 600);

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Switch layers
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    setLoading(true);

    // Remove all overlays
    Object.values(overlaysRef.current).forEach((lg) => {
      if (map.hasLayer(lg)) map.removeLayer(lg);
    });

    // Add selected overlay
    if (activeLayer !== "truecolor" && overlaysRef.current[activeLayer]) {
      overlaysRef.current[activeLayer].addTo(map);
    }

    setTimeout(() => setLoading(false), 400);
  }, [activeLayer]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {loading && (
        <div className="pointer-events-none absolute inset-0 z-[1000] flex items-center justify-center bg-background/60">
          <div className="flex items-center gap-2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <span className="font-mono text-xs text-muted-foreground">Loading tiles...</span>
          </div>
        </div>
      )}
    </div>
  );
};
