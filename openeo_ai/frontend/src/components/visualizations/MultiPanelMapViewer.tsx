import { useState, useCallback, useRef, useEffect } from "react";
import { MapContainer, TileLayer, ImageOverlay, useMap, useMapEvents } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import type { MapVisualization, MultiPanelVisualization } from "@/types";
import { cn, authUrl } from "@/lib/utils";
import { useAuth } from "@/components/auth/useAuth";
import { ColorLegend } from "./map-tools/ColorLegend";
import { detectResultType } from "@/hooks/useResultType";

interface PanelItem {
  label: string;
  visualization: MapVisualization;
}

interface MultiPanelMapViewerProps {
  data: MultiPanelVisualization;
  className?: string;
  title?: string;
}

// Sync component that broadcasts map movements to other panels
function MapSync({
  center,
  zoom,
  onMove,
  panelIndex,
}: {
  center: [number, number];
  zoom: number;
  onMove: (center: [number, number], zoom: number, source: number) => void;
  panelIndex: number;
}) {
  const map = useMap();
  const isUpdating = useRef(false);

  useMapEvents({
    moveend: () => {
      if (isUpdating.current) return;
      const c = map.getCenter();
      onMove([c.lat, c.lng], map.getZoom(), panelIndex);
    },
    zoomend: () => {
      if (isUpdating.current) return;
      const c = map.getCenter();
      onMove([c.lat, c.lng], map.getZoom(), panelIndex);
    },
  });

  useEffect(() => {
    isUpdating.current = true;
    map.setView(center, zoom, { animate: false });
    const t = setTimeout(() => {
      isUpdating.current = false;
    }, 100);
    return () => clearTimeout(t);
  }, [center, zoom, map]);

  return null;
}

function getGridCols(count: number): string {
  if (count <= 2) return "grid-cols-2";
  if (count <= 3) return "grid-cols-3";
  if (count <= 4) return "grid-cols-2";
  if (count <= 6) return "grid-cols-3";
  return "grid-cols-3";
}

function getGridRows(count: number): string {
  if (count <= 3) return "grid-rows-1";
  if (count <= 6) return "grid-rows-2";
  return "grid-rows-3";
}

export function MultiPanelMapViewer({ data, className, title }: MultiPanelMapViewerProps) {
  const { panels } = data;
  // Overlay tiles load via <img>-style requests that cannot carry an
  // Authorization header, so the token must ride the URL (authUrl).
  const { accessToken: authToken } = useAuth();
  const [syncCenter, setSyncCenter] = useState<[number, number]>([20, 0]);
  const [syncZoom, setSyncZoom] = useState(10);
  const [initialized, setInitialized] = useState(false);
  const sourceRef = useRef<number>(-1);

  // Initialize center from first panel's bounds
  useEffect(() => {
    if (initialized || panels.length === 0) return;
    const firstViz = panels[0]!.visualization;
    if (firstViz.bounds) {
      const b = firstViz.bounds;
      const centerLat = (b[0][0] + b[1][0]) / 2;
      const centerLng = (b[0][1] + b[1][1]) / 2;
      setSyncCenter([centerLat, centerLng]);
      setInitialized(true);
    }
  }, [panels, initialized]);

  const handleMove = useCallback((center: [number, number], zoom: number, source: number) => {
    sourceRef.current = source;
    setSyncCenter(center);
    setSyncZoom(zoom);
  }, []);

  if (panels.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        No panels to display
      </div>
    );
  }

  const gridCols = getGridCols(panels.length);
  const gridRows = getGridRows(panels.length);

  return (
    <div className={cn("flex h-full flex-col gap-2", className)}>
      {title && (
        <div className="flex items-center gap-2 px-1">
          <h3 className="text-sm font-medium">{title}</h3>
          <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {panels.length} panels
          </span>
        </div>
      )}
      <div className={cn("grid flex-1 gap-1", gridCols, gridRows)}>
        {panels.map((panel: PanelItem, idx: number) => {
          const viz = panel.visualization;
          const bounds: LatLngBoundsExpression | undefined = viz.bounds
            ? [viz.bounds[0], viz.bounds[1]]
            : undefined;

          return (
            <div key={idx} className="relative flex flex-col overflow-hidden rounded border">
              <div className="absolute left-2 top-2 z-[1000]">
                <span className="rounded bg-black/70 px-2 py-0.5 text-xs text-white">
                  {panel.label}
                </span>
              </div>
              <div className="flex-1">
                <MapContainer
                  center={syncCenter}
                  zoom={syncZoom}
                  className="h-full w-full"
                  zoomControl={false}
                  attributionControl={false}
                >
                  <TileLayer
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    attribution=""
                  />
                  <MapSync
                    center={syncCenter}
                    zoom={syncZoom}
                    onMove={handleMove}
                    panelIndex={idx}
                  />
                  {viz.url && bounds && (
                    <ImageOverlay
                      url={authUrl(viz.url, authToken)}
                      bounds={bounds}
                      opacity={viz.opacity ?? 0.8}
                    />
                  )}
                </MapContainer>
              </div>
              {viz.colormap && (
                <div className="p-1">
                  <ColorLegend
                    colormap={viz.colormap}
                    vmin={viz.vmin ?? 0}
                    vmax={viz.vmax ?? 1}
                    resultConfig={detectResultType(viz, panel.label)}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
