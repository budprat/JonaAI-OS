import { useRef, useState, useEffect, useCallback, useMemo } from "react";
import {
  MapContainer,
  TileLayer,
  ImageOverlay,
  GeoJSON,
  CircleMarker,
  useMap,
} from "react-leaflet";
import { LatLngBoundsExpression } from "leaflet";
import {
  Layers,
  ZoomIn,
  ZoomOut,
  Download,
  Square,
  X,
  Crosshair,
  MousePointer,
  Search,
  Eye,
  EyeOff,
  ListTree,
  Trash2,
  SplitSquareHorizontal,
  Copy,
  Check,
  MapPin,
  Pentagon,
} from "lucide-react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapVisualization, BBox, SavedJobDetail } from "@/types";
import { cn, authUrl } from "@/lib/utils";
import { detectResultType } from "@/hooks/useResultType";
import { ColorLegend } from "./map-tools/ColorLegend";
import { OpacitySlider } from "./map-tools/OpacitySlider";
import { PixelInspector } from "./map-tools/PixelInspector";
import { StatsInfoPanel } from "./map-tools/StatsInfoPanel";
import { useAuth } from "@/components/auth/useAuth";
import {
  IndexLegend,
  InterpretationBadge,
  NaNWarning,
} from "./MapOverlay";
import {
  BboxDrawHandler,
  PolygonDrawHandler,
  computePolygonAreaKm2,
  formatArea,
} from "./map-tools/draw-handlers";

type DrawMode = "none" | "bbox" | "polygon";

interface MapLayer {
  id: string;
  data: MapVisualization;
  title?: string;
  visible: boolean;
  opacity: number;
}

interface MapViewerProps {
  data?: MapVisualization;
  title?: string;
  layers?: { id: string; data: MapVisualization; title?: string }[];
  className?: string;
  onBboxChange?: (bbox: BBox | null) => void;
  bbox?: BBox | null;
  /** Called when a layer is removed via the layer panel UI. */
  onRemoveLayer?: (layerId: string) => void;
  /** Called when the user wants to compare two layers side by side. */
  onCompare?: (layerA: string, layerB: string) => void;
  /** Saved-result ID used to fetch interpretation overlays. */
  saveId?: string;
}

const BASEMAPS = {
  osm: {
    name: "Streets",
    url: "/tile-proxy/osm/{z}/{y}/{x}",
    attribution: "&copy; OpenStreetMap contributors",
  },
  satellite: {
    name: "Satellite",
    url: "/tile-proxy/satellite/{z}/{y}/{x}",
    attribution: "&copy; Esri",
  },
  terrain: {
    name: "Terrain",
    url: "/tile-proxy/terrain/{z}/{y}/{x}",
    attribution: "&copy; OpenTopoMap",
  },
};

const COLORMAPS = [
  "viridis",
  "ndvi",
  "coolwarm",
  "plasma",
  "inferno",
  "terrain",
  "magma",
  "cividis",
  "rdylgn",
  "rdylbu",
  "spectral",
  "rdbu",
  "ylgnbu",
  "grayscale",
  "hot",
  "ylorrd",
  "ylorbr",
  "set2",
];

/** Detect if a string looks like "lat, lng" coordinates.
 *  Accepts: "28.6139, 77.2090", "28.6139 77.2090", "28.6139,77.2090"
 *  Returns parsed { lat, lng } or null. */
function parseCoordinateInput(
  input: string,
): { lat: number; lng: number } | null {
  const trimmed = input.trim();
  // Match two numbers separated by comma (with optional spaces) or whitespace
  const match = trimmed.match(
    /^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/,
  );
  if (!match) return null;
  const lat = parseFloat(match[1]!);
  const lng = parseFloat(match[2]!);
  // Validate ranges: lat [-90, 90], lng [-180, 180]
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return { lat, lng };
}

// Sentinel value used to mark a search result as a coordinate entry
const COORD_RESULT_MARKER = "__coordinate__";


// Fix Leaflet sizing when container mounts or resizes
function MapResizer() {
  const map = useMap();
  useEffect(() => {
    // Invalidate after layout settles (flex containers need a tick)
    const timer = setTimeout(() => {
      map.invalidateSize();
    }, 150);

    // Watch for container resize (tab switches, panel resizes)
    const observer = new ResizeObserver(() => {
      map.invalidateSize();
    });
    const container = map.getContainer();
    if (container) {
      observer.observe(container);
    }

    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [map]);
  return null;
}

// Component to fit map bounds when they change
function FitBounds({ bounds }: { bounds: LatLngBoundsExpression }) {
  const map = useMap();
  useEffect(() => {
    if (bounds) {
      map.fitBounds(bounds, { padding: [20, 20] });
    }
  }, [map, bounds]);
  return null;
}


// Convert bounds from various formats to Leaflet format
function convertBounds(b: unknown): LatLngBoundsExpression {
  const defaultBounds: LatLngBoundsExpression = [
    [-60, -180],
    [80, 180],
  ];
  if (!b) return defaultBounds;
  if (Array.isArray(b) && b.length === 2 && Array.isArray(b[0]))
    return b as LatLngBoundsExpression;
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
  return defaultBounds;
}

// Control button component
function ControlButton({
  onClick,
  title,
  active = false,
  danger = false,
  ariaLabel,
  children,
}: {
  onClick: () => void;
  title: string;
  active?: boolean;
  danger?: boolean;
  ariaLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={title}
      aria-label={ariaLabel || title}
      className={cn(
        "flex h-8 w-8 items-center justify-center rounded-md transition-colors",
        "border border-black/10 shadow-sm",
        active
          ? "bg-primary text-white"
          : danger
            ? "bg-white text-red-500 hover:bg-red-50"
            : "bg-white text-gray-700 hover:bg-gray-50",
      )}
    >
      {children}
    </button>
  );
}

export function MapViewer({
  data,
  title,
  layers: externalLayers,
  className,
  onBboxChange,
  bbox,
  onRemoveLayer,
  onCompare,
  saveId,
}: MapViewerProps) {
  const { accessToken: authToken } = useAuth();
  const [basemap, setBasemap] = useState<keyof typeof BASEMAPS>("satellite");
  const [colormap, setColormap] = useState(data?.colormap || "viridis");
  const [imageUrl, setImageUrl] = useState(data?.url ? authUrl(data.url, authToken) : "");
  const [imageError, setImageError] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState<DrawMode>("none");
  const [showBasemapPicker, setShowBasemapPicker] = useState(false);
  const [showColormapPicker, setShowColormapPicker] = useState(false);
  const [opacity, setOpacity] = useState(data?.opacity ?? 0.5);
  const [inspectorActive, setInspectorActive] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<
    { display_name: string; lat: string; lon: string; boundingbox: string[] }[]
  >([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [showLayerPanel, setShowLayerPanel] = useState(false);
  const [mapLayers, setMapLayers] = useState<MapLayer[]>([]);
  const [coordMarker, setCoordMarker] = useState<{
    lat: number;
    lng: number;
  } | null>(null);
  const [copiedCoords, setCopiedCoords] = useState(false);
  const [polygonPoints, setPolygonPoints] = useState<L.LatLng[] | null>(null);
  // Track IDs removed via UI so reconciliation doesn't re-add them
  const removedIdsRef = useRef<Set<string>>(new Set());
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  // ── Interpretation overlay state (fetched from saved-jobs when saveId provided) ──
  const [savedDetail, setSavedDetail] = useState<SavedJobDetail | null>(null);

  useEffect(() => {
    if (!saveId || !authToken) {
      setSavedDetail(null);
      return;
    }
    let cancelled = false;
    fetch(`/saved-jobs/${saveId}`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled) setSavedDetail(d as SavedJobDetail | null);
      })
      .catch(() => {
        if (!cancelled) setSavedDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [saveId, authToken]);

  // Build layers from props — reconcile to preserve visibility/opacity state
  useEffect(() => {
    if (!externalLayers) return;
    setMapLayers((prev) => {
      const newIds = new Set(externalLayers.map((l) => l.id));
      // Prune removed set: if external no longer has it, stop tracking
      removedIdsRef.current.forEach((rid) => {
        if (!newIds.has(rid)) removedIdsRef.current.delete(rid);
      });
      // Keep existing layers that still exist, preserving visibility/opacity state
      const kept = prev
        .filter((l) => newIds.has(l.id))
        .map((l) => {
          const ext = externalLayers.find((e) => e.id === l.id);
          return ext ? { ...l, data: ext.data, title: ext.title } : l;
        });
      // Add truly new layers (skip ones removed via UI)
      const existingIds = new Set(kept.map((l) => l.id));
      const added = externalLayers
        .filter(
          (l) => !existingIds.has(l.id) && !removedIdsRef.current.has(l.id),
        )
        .map((l) => ({
          ...l,
          visible: true,
          opacity: l.data.opacity ?? 0.5,
        }));
      return [...kept, ...added];
    });
  }, [externalLayers]);

  useEffect(() => {
    if (data?.url) {
      setImageUrl(authUrl(data.url, authToken));
      setImageError(null);
    }
    if (data?.opacity !== undefined) setOpacity(data.opacity);
  }, [data?.url, data?.opacity, authToken]);

  const bounds = data ? convertBounds(data.bounds) : undefined;
  const resultConfig = detectResultType(data, title);
  const hasRaster = !!(data?.type === "raster" && imageUrl && bounds);
  const hasValueRange = data?.vmin != null && data?.vmax != null;
  const hasMultipleLayers = mapLayers.length >= 1;

  // Count visible layers for comparison button
  const visibleLayers = useMemo(
    () => mapLayers.filter((l) => l.visible),
    [mapLayers],
  );
  const canCompare = visibleLayers.length >= 2 && !!onCompare;

  const toggleLayerVisibility = useCallback((layerId: string) => {
    setMapLayers((prev) =>
      prev.map((l) => (l.id === layerId ? { ...l, visible: !l.visible } : l)),
    );
  }, []);

  const updateLayerOpacity = useCallback(
    (layerId: string, newOpacity: number) => {
      setMapLayers((prev) =>
        prev.map((l) => (l.id === layerId ? { ...l, opacity: newOpacity } : l)),
      );
    },
    [],
  );

  const removeLayer = useCallback(
    (layerId: string) => {
      removedIdsRef.current.add(layerId);
      setMapLayers((prev) => prev.filter((l) => l.id !== layerId));
      onRemoveLayer?.(layerId);
    },
    [onRemoveLayer],
  );

  const handleCompare = useCallback(() => {
    if (!onCompare || visibleLayers.length < 2) return;
    onCompare(visibleLayers[0]!.id, visibleLayers[1]!.id);
  }, [onCompare, visibleLayers]);

  const handleColormapChange = (newColormap: string) => {
    setColormap(newColormap);
    setShowColormapPicker(false);
    if (data?.source) {
      setImageUrl(
        authUrl(`/render-raster?path=${encodeURIComponent(data.source)}&colormap=${encodeURIComponent(newColormap)}`, authToken),
      );
    } else if (data?.url && !data.url.startsWith("data:")) {
      const baseUrl = data.url.split("?")[0];
      const params = new URLSearchParams(data.url.split("?")[1] || "");
      params.set("colormap", newColormap);
      setImageUrl(authUrl(`${baseUrl}?${params.toString()}`, authToken));
    }
  };

  const handleDownload = () => {
    if (imageUrl) window.open(imageUrl, "_blank");
  };

  const handleBboxDrawEnd = useCallback(() => setDrawMode("none"), []);
  const handlePolygonDrawEnd = useCallback(() => setDrawMode("none"), []);
  const handleBboxChange = useCallback(
    (b: BBox | null) => {
      onBboxChange?.(b);
    },
    [onBboxChange],
  );
  const handlePolygonUpdate = useCallback((points: L.LatLng[] | null) => {
    setPolygonPoints(points);
  }, []);

  const polygonArea = useMemo(() => {
    if (!polygonPoints || polygonPoints.length < 3) return null;
    return computePolygonAreaKm2(polygonPoints);
  }, [polygonPoints]);

  const handleSearchInput = useCallback((value: string) => {
    setSearchQuery(value);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (!value.trim()) {
      setSearchResults([]);
      return;
    }

    // Check if input looks like coordinates
    const coords = parseCoordinateInput(value);
    if (coords) {
      const coordResult = {
        display_name: `Go to ${coords.lat}, ${coords.lng}`,
        lat: String(coords.lat),
        lon: String(coords.lng),
        boundingbox: [COORD_RESULT_MARKER],
      };
      setSearchResults([coordResult]);
      setSearching(false);
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      return;
    }

    searchTimerRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(value.trim())}`,
        );
        const data = await res.json();
        setSearchResults(data);
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 400);
  }, []);

  const handleSelectPlace = useCallback(
    (result: { lat: string; lon: string; boundingbox: string[] }) => {
      const map = mapRef.current;
      if (!map) return;

      const isCoordResult = result.boundingbox[0] === COORD_RESULT_MARKER;

      if (isCoordResult) {
        const lat = parseFloat(result.lat);
        const lng = parseFloat(result.lon);
        map.flyTo([lat, lng], 12, { animate: true, duration: 1.5 });
        setCoordMarker({ lat, lng });
      } else {
        const [south, north, west, east] = result.boundingbox.map(Number) as [number, number, number, number];
        map.fitBounds(
          [
            [south, west],
            [north, east],
          ],
          { padding: [30, 30], animate: true },
        );
        setCoordMarker(null);
      }

      setSearchOpen(false);
      setSearchQuery("");
      setSearchResults([]);
    },
    [],
  );

  const handleCopyCoordinates = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const center = map.getCenter();
    const text = `${center.lat.toFixed(6)}, ${center.lng.toFixed(6)}`;
    navigator.clipboard.writeText(text).then(() => {
      setCopiedCoords(true);
      setTimeout(() => setCopiedCoords(false), 2000);
    });
  }, []);

  return (
    <div
      className={cn("relative overflow-hidden rounded-lg", className)}
      style={{ height: "100%", minHeight: "300px" }}
    >
      {/* Map fills the container */}
      <MapContainer
        center={bounds ? undefined : [20, 0]}
        zoom={bounds ? undefined : 2}
        bounds={bounds}
        style={{ height: "100%", width: "100%" }}
        zoomControl={false}
        ref={mapRef}
      >
        <MapResizer />
        <TileLayer
          key={basemap}
          url={BASEMAPS[basemap].url}
          attribution={BASEMAPS[basemap].attribution}
        />
        {bounds && <FitBounds bounds={bounds} />}

        <BboxDrawHandler
          isDrawing={drawMode === "bbox"}
          bbox={bbox ?? null}
          onBboxChange={handleBboxChange}
          onDrawEnd={handleBboxDrawEnd}
        />

        <PolygonDrawHandler
          isDrawing={drawMode === "polygon"}
          onBboxChange={handleBboxChange}
          onDrawEnd={handlePolygonDrawEnd}
          onPolygonUpdate={handlePolygonUpdate}
        />

        {/* Single-layer mode (backward compatible) */}
        {data?.type === "raster" &&
          imageUrl &&
          bounds &&
          mapLayers.length === 0 && (
            <ImageOverlay
              url={imageUrl}
              bounds={bounds}
              opacity={opacity}
              eventHandlers={{
                error: () => setImageError("Failed to load raster image"),
                load: () => setImageError(null),
              }}
            />
          )}

        {/* Multi-layer mode */}
        {mapLayers.map((layer) => {
          if (!layer.visible || layer.data.type !== "raster" || !layer.data.url)
            return null;
          const layerBounds = convertBounds(layer.data.bounds);
          if (!layerBounds) return null;
          return (
            <ImageOverlay
              key={layer.id}
              url={authUrl(layer.data.url, authToken)}
              bounds={layerBounds}
              opacity={layer.opacity}
            />
          );
        })}

        {data?.type === "vector" && data.geojson && (
          <GeoJSON
            data={data.geojson}
            style={(feature) => {
              const category =
                feature?.properties?.category ||
                feature?.properties?.class ||
                "default";
              const colors: Record<string, string> = {
                vegetation: "#22c55e",
                water: "#3b82f6",
                urban: "#f59e0b",
                bare_soil: "#a16207",
                cloud: "#94a3b8",
                fire: "#ef4444",
                snow: "#e2e8f0",
                default: "#8b5cf6",
              };
              return {
                color: colors[category] || colors.default,
                weight: 2,
                fillOpacity: 0.15,
                fillColor: colors[category] || colors.default,
              };
            }}
            onEachFeature={(feature, layer) => {
              const props = feature.properties || {};
              // Sanitize values to prevent XSS from untrusted GeoJSON properties
              const esc = (s: unknown) =>
                String(s ?? "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;");
              const label = esc(
                props.label || props.class || props.category || "Detection",
              );
              const confidence = props.confidence
                ? ` (${(Number(props.confidence) * 100).toFixed(0)}%)`
                : "";
              const area = props.area
                ? `<br/>Area: ${esc(props.area)}`
                : "";
              layer.bindTooltip(
                `<strong>${label}</strong>${confidence}${area}`,
                {
                  sticky: true,
                  className: "ea-detection-tooltip",
                },
              );
            }}
          />
        )}

        {/* Coordinate marker — shows after navigating to typed coordinates */}
        {coordMarker && (
          <CircleMarker
            center={[coordMarker.lat, coordMarker.lng]}
            radius={8}
            pathOptions={{
              color: "#ef4444",
              weight: 2,
              fillColor: "#ef4444",
              fillOpacity: 0.3,
            }}
          />
        )}

        {/* Pixel inspector — needs useMap() so rendered inside MapContainer */}
        {hasRaster && (
          <PixelInspector
            active={inspectorActive}
            bounds={data?.bounds}
            vmin={data?.vmin}
            vmax={data?.vmax}
            source={data?.source}
            resultConfig={resultConfig}
          />
        )}
      </MapContainer>

      {/* Title overlay */}
      {title && (
        <div className="absolute left-12 top-3 z-[1000] rounded-md bg-white/90 px-3 py-1 text-sm font-medium shadow-sm backdrop-blur-sm">
          {title}
        </div>
      )}

      {/* Error overlay */}
      {imageError && (
        <div
          className="absolute bottom-3 left-3 z-[1000] rounded-md bg-red-500/90 px-3 py-1.5 text-xs font-medium text-white shadow"
          role="alert"
        >
          {imageError}
        </div>
      )}

      {/* Drawing mode hint overlay */}
      {drawMode === "polygon" && (
        <div className="absolute left-1/2 top-3 z-[1000] -translate-x-1/2 rounded-md bg-emerald-600/90 px-3 py-1.5 text-xs font-medium text-white shadow-sm backdrop-blur-sm">
          Click to add points. Double-click or click first point to close. Esc to cancel. Ctrl+Z to undo.
        </div>
      )}

      {/* Left controls: search + bbox draw + polygon draw + inspector */}
      <div
        className="absolute left-3 top-3 z-[1000] flex flex-col gap-1.5"
        role="toolbar"
        aria-label="Map drawing tools"
      >
        {/* Place search */}
        <div className="relative">
          {searchOpen ? (
            <div className="flex flex-col">
              <div className="flex items-center gap-1">
                <div className="flex items-center rounded-md border border-black/10 bg-white shadow-sm">
                  <Search size={14} className="ml-2 text-gray-400" />
                  <input
                    autoFocus
                    type="text"
                    placeholder="Search place or coords..."
                    value={searchQuery}
                    onChange={(e) => handleSearchInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        setSearchOpen(false);
                        setSearchQuery("");
                        setSearchResults([]);
                      }
                      if (e.key === "Enter" && searchResults.length > 0)
                        handleSelectPlace(searchResults[0]!);
                    }}
                    className="w-48 bg-transparent px-2 py-1.5 text-xs outline-none"
                    aria-label="Search for a place or enter coordinates"
                  />
                  {searching && (
                    <div
                      className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-primary"
                      role="status"
                      aria-label="Searching"
                    />
                  )}
                </div>
                <ControlButton
                  onClick={() => {
                    setSearchOpen(false);
                    setSearchQuery("");
                    setSearchResults([]);
                  }}
                  title="Close search"
                  ariaLabel="Close search"
                >
                  <X size={14} />
                </ControlButton>
              </div>
              {searchResults.length > 0 && (
                <div
                  className="mt-1 max-h-48 w-60 overflow-y-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg"
                  role="listbox"
                  aria-label="Search results"
                >
                  {searchResults.map((r, i) => {
                    const isCoord = r.boundingbox[0] === COORD_RESULT_MARKER;
                    return (
                      <button
                        key={i}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSelectPlace(r);
                        }}
                        role="option"
                        aria-selected={false}
                        className={cn(
                          "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-gray-50",
                          isCoord
                            ? "font-medium text-primary"
                            : "text-gray-700",
                        )}
                      >
                        {isCoord && (
                          <MapPin size={12} className="shrink-0 text-primary" />
                        )}
                        <span className="line-clamp-2">{r.display_name}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <ControlButton
              onClick={() => setSearchOpen(true)}
              title="Search place or coordinates"
              ariaLabel="Search place or coordinates"
            >
              <Search size={15} />
            </ControlButton>
          )}
        </div>

        {onBboxChange && (
          <>
            {/* Rectangle bbox draw button */}
            <ControlButton
              onClick={() => {
                setDrawMode(drawMode === "bbox" ? "none" : "bbox");
                setInspectorActive(false);
                setPolygonPoints(null);
              }}
              title={
                drawMode === "bbox"
                  ? "Cancel rectangle drawing"
                  : "Draw bounding box (rectangle)"
              }
              ariaLabel={
                drawMode === "bbox"
                  ? "Cancel drawing bounding box"
                  : "Draw bounding box rectangle"
              }
              active={drawMode === "bbox"}
            >
              {drawMode === "bbox" ? (
                <Crosshair size={15} />
              ) : (
                <Square size={15} />
              )}
            </ControlButton>

            {/* Polygon draw button */}
            <ControlButton
              onClick={() => {
                setDrawMode(drawMode === "polygon" ? "none" : "polygon");
                setInspectorActive(false);
                setPolygonPoints(null);
              }}
              title={
                drawMode === "polygon"
                  ? "Cancel polygon drawing"
                  : "Draw polygon AOI"
              }
              ariaLabel={
                drawMode === "polygon"
                  ? "Cancel drawing polygon"
                  : "Draw polygon area of interest"
              }
              active={drawMode === "polygon"}
            >
              {drawMode === "polygon" ? (
                <Crosshair size={15} />
              ) : (
                <Pentagon size={15} />
              )}
            </ControlButton>

            {/* Clear bbox/polygon button */}
            {(bbox || polygonPoints) && (
              <ControlButton
                onClick={() => {
                  onBboxChange(null);
                  setPolygonPoints(null);
                }}
                title="Clear AOI"
                ariaLabel="Clear area of interest"
                danger
              >
                <X size={15} />
              </ControlButton>
            )}
          </>
        )}

        {hasRaster && (
          <ControlButton
            onClick={() => {
              setInspectorActive(!inspectorActive);
              setDrawMode("none");
            }}
            title={
              inspectorActive
                ? "Disable pixel inspector"
                : "Inspect pixel values"
            }
            ariaLabel={
              inspectorActive
                ? "Disable pixel inspector"
                : "Enable pixel inspector"
            }
            active={inspectorActive}
          >
            <MousePointer size={15} />
          </ControlButton>
        )}
      </div>

      {/* Bbox / Polygon coordinates badge */}
      {bbox && (
        <div className="absolute bottom-3 left-1/2 z-[1000] -translate-x-1/2 rounded-full bg-white/90 px-4 py-1.5 text-xs font-medium shadow-sm backdrop-blur-sm">
          <span className="text-gray-500">
            {polygonPoints ? "Polygon" : "BBox"}:
          </span>{" "}
          <span className="tabular-nums">
            {bbox.west.toFixed(3)}, {bbox.south.toFixed(3)},{" "}
            {bbox.east.toFixed(3)}, {bbox.north.toFixed(3)}
          </span>
          {polygonArea != null && (
            <>
              {" "}
              <span className="text-gray-400">|</span>{" "}
              <span className="text-gray-500">Area:</span>{" "}
              <span className="tabular-nums">{formatArea(polygonArea)}</span>
            </>
          )}
        </div>
      )}

      {/* Right controls: zoom, basemap, colormap, download, stats */}
      <div
        className="absolute right-3 top-3 z-[1000] flex flex-col gap-1.5"
        role="toolbar"
        aria-label="Map controls"
      >
        {/* Zoom */}
        <div className="flex flex-col gap-0.5 rounded-md border border-black/10 bg-white shadow-sm">
          <button
            onClick={() => mapRef.current?.zoomIn()}
            title="Zoom in"
            aria-label="Zoom in"
            className="flex h-8 w-8 items-center justify-center rounded-t-md text-gray-700 hover:bg-gray-50"
          >
            <ZoomIn size={15} />
          </button>
          <div className="mx-1.5 h-px bg-gray-200" />
          <button
            onClick={() => mapRef.current?.zoomOut()}
            title="Zoom out"
            aria-label="Zoom out"
            className="flex h-8 w-8 items-center justify-center rounded-b-md text-gray-700 hover:bg-gray-50"
          >
            <ZoomOut size={15} />
          </button>
        </div>

        {/* Copy map center coordinates */}
        <ControlButton
          onClick={handleCopyCoordinates}
          title={
            copiedCoords ? "Coordinates copied!" : "Copy map center coordinates"
          }
          ariaLabel="Copy current map center coordinates to clipboard"
          active={copiedCoords}
        >
          {copiedCoords ? <Check size={15} /> : <Copy size={15} />}
        </ControlButton>

        {/* Basemap picker */}
        <div className="relative">
          <ControlButton
            onClick={() => {
              setShowBasemapPicker(!showBasemapPicker);
              setShowColormapPicker(false);
            }}
            title="Change basemap"
            ariaLabel="Change basemap"
            active={showBasemapPicker}
          >
            <Layers size={15} />
          </ControlButton>
          {showBasemapPicker && (
            <div
              className="absolute right-10 top-0 w-32 rounded-md border border-gray-200 bg-white py-1 shadow-lg"
              role="menu"
              aria-label="Basemap options"
            >
              {Object.entries(BASEMAPS).map(([key, { name }]) => (
                <button
                  key={key}
                  onClick={() => {
                    setBasemap(key as keyof typeof BASEMAPS);
                    setShowBasemapPicker(false);
                  }}
                  role="menuitem"
                  className={cn(
                    "w-full px-3 py-1.5 text-left text-xs transition-colors hover:bg-gray-50",
                    basemap === key
                      ? "font-semibold text-primary"
                      : "text-gray-700",
                  )}
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Colormap picker — only when raster data */}
        {data?.type === "raster" && (
          <div className="relative">
            <ControlButton
              onClick={() => {
                setShowColormapPicker(!showColormapPicker);
                setShowBasemapPicker(false);
              }}
              title="Change colormap"
              ariaLabel="Change colormap"
              active={showColormapPicker}
            >
              <span className="text-[10px] font-bold leading-none">CM</span>
            </ControlButton>
            {showColormapPicker && (
              <div
                className="absolute right-10 top-0 w-28 rounded-md border border-gray-200 bg-white py-1 shadow-lg"
                role="menu"
                aria-label="Colormap options"
              >
                {COLORMAPS.map((cm) => (
                  <button
                    key={cm}
                    onClick={() => handleColormapChange(cm)}
                    role="menuitem"
                    className={cn(
                      "w-full px-3 py-1.5 text-left text-xs transition-colors hover:bg-gray-50",
                      colormap === cm
                        ? "font-semibold text-primary"
                        : "text-gray-700",
                    )}
                  >
                    {cm}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Multi-layer panel — only when multiple layers */}
        {hasMultipleLayers && (
          <div className="relative">
            <ControlButton
              onClick={() => {
                setShowLayerPanel(!showLayerPanel);
                setShowBasemapPicker(false);
                setShowColormapPicker(false);
              }}
              title="Layer controls"
              ariaLabel="Toggle layer controls panel"
              active={showLayerPanel}
            >
              <ListTree size={15} />
            </ControlButton>
            {showLayerPanel && (
              <div className="absolute right-10 top-0 w-56 rounded-md border border-gray-200 bg-white py-2 shadow-lg">
                <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                  Layers ({mapLayers.length})
                </div>
                {mapLayers.map((layer) => (
                  <div key={layer.id} className="px-3 py-1.5">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => toggleLayerVisibility(layer.id)}
                        className="shrink-0 text-gray-500 hover:text-gray-700"
                        title={layer.visible ? "Hide layer" : "Show layer"}
                        aria-label={
                          layer.visible
                            ? `Hide layer ${layer.title || layer.id}`
                            : `Show layer ${layer.title || layer.id}`
                        }
                      >
                        {layer.visible ? (
                          <Eye size={13} />
                        ) : (
                          <EyeOff size={13} />
                        )}
                      </button>
                      <button
                        onClick={() => removeLayer(layer.id)}
                        className="shrink-0 text-gray-400 hover:text-red-500 transition-colors"
                        title="Remove layer"
                        aria-label={`Remove layer ${layer.title || layer.id}`}
                      >
                        <Trash2 size={12} />
                      </button>
                      <span
                        className={cn(
                          "flex-1 truncate text-xs",
                          layer.visible ? "text-gray-700" : "text-gray-400",
                        )}
                      >
                        {layer.title || layer.id}
                      </span>
                    </div>
                    {layer.visible && (
                      <div className="ml-5 mt-1 flex items-center gap-2">
                        <span className="text-[10px] text-gray-400">
                          Opacity
                        </span>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={layer.opacity}
                          onChange={(e) =>
                            updateLayerOpacity(
                              layer.id,
                              parseFloat(e.target.value),
                            )
                          }
                          className="h-1 w-full cursor-pointer accent-primary"
                          aria-label={`Opacity for layer ${layer.title || layer.id}`}
                        />
                        <span className="text-[10px] tabular-nums text-gray-500">
                          {Math.round(layer.opacity * 100)}%
                        </span>
                      </div>
                    )}
                  </div>
                ))}

                {/* Compare button — appears when 2+ visible layers */}
                {canCompare && (
                  <div className="mx-3 mt-2 border-t border-gray-100 pt-2">
                    <button
                      onClick={handleCompare}
                      className="flex w-full items-center justify-center gap-1.5 rounded-md bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                      title="Compare first two visible layers side by side"
                      aria-label="Compare first two visible layers side by side"
                    >
                      <SplitSquareHorizontal size={13} />
                      Compare Layers
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Download — only when there's an image */}
        {imageUrl && data && (
          <ControlButton
            onClick={handleDownload}
            title="Download image"
            ariaLabel="Download raster image"
          >
            <Download size={15} />
          </ControlButton>
        )}

        {/* Stats info panel — only when raster stats available */}
        {hasRaster && data?.stats && (
          <StatsInfoPanel stats={data.stats} resultConfig={resultConfig} />
        )}

        {/* Opacity slider — only when raster data (single layer mode) */}
        {hasRaster && !hasMultipleLayers && (
          <OpacitySlider value={opacity} onChange={setOpacity} />
        )}
      </div>

      {/* Color legend — bottom left, above error overlay */}
      {hasRaster && hasValueRange && (
        <div className="absolute bottom-10 left-3 z-[1000]">
          <ColorLegend
            vmin={data!.vmin!}
            vmax={data!.vmax!}
            colormap={colormap}
            resultConfig={resultConfig}
            stats={data?.stats}
          />
        </div>
      )}

      {/* Interpretation overlays — enriched from saved-job detail */}
      {savedDetail?.interpretation && (
        <div className="absolute left-3 top-3 z-[1000]">
          <InterpretationBadge interpretation={savedDetail.interpretation} />
        </div>
      )}

      {savedDetail?.interpretation?.category_breakdown &&
        savedDetail.interpretation.category_breakdown.length > 0 && (
        <div className="absolute bottom-10 left-3 z-[1000]">
          <IndexLegend
            index={savedDetail.interpreted_index}
            colormap={colormap}
            categoryBreakdown={savedDetail.interpretation.category_breakdown}
          />
        </div>
      )}

      {savedDetail?.statistics &&
        typeof (savedDetail.statistics as Record<string, unknown>).nan_percent ===
          "number" && (
        <div className="absolute left-1/2 top-3 z-[1000] -translate-x-1/2">
          <NaNWarning
            nanPercent={
              (savedDetail.statistics as Record<string, unknown>).nan_percent as number
            }
          />
        </div>
      )}
    </div>
  );
}
