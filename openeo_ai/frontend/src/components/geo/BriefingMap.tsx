import React, { useEffect, useRef, useState } from "react";
import L from "leaflet";

interface Zone {
  name: string;
  coordinates: L.LatLngExpression[];
  color: string;
}

interface BriefingMapProps {
  center: L.LatLngExpression;
  zoom?: number;
  zones?: Zone[];
  highlightedZone?: string | null;
  onZoneClick?: (zoneName: string) => void;
}

export const BriefingMap: React.FC<BriefingMapProps> = ({
  center,
  zoom = 15,
  zones = [],
  highlightedZone,
  onZoneClick,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const zoneLayersRef = useRef<Record<string, L.Polygon>>({});
  const [ready, setReady] = useState(false);

  // Initialize map
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: false,
    }).setView(center, zoom);

    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19 },
    ).addTo(map);

    mapRef.current = map;

    setTimeout(() => {
      map.invalidateSize();
      setReady(true);
    }, 200);

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Manage zone polygons
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;

    // Clear existing layers
    Object.values(zoneLayersRef.current).forEach((layer) =>
      map.removeLayer(layer),
    );
    zoneLayersRef.current = {};

    zones.forEach((zone) => {
      const layer = L.polygon(zone.coordinates, {
        color: zone.color,
        fillColor: zone.color,
        fillOpacity: 0.15,
        weight: 1,
      }).addTo(map);

      if (onZoneClick) {
        layer.on("click", () => onZoneClick(zone.name));
      }

      zoneLayersRef.current[zone.name] = layer;
    });
  }, [zones, ready]);

  // Handle zone highlighting
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;

    // Reset all zones
    Object.values(zoneLayersRef.current).forEach((layer) => {
      layer.setStyle({ fillOpacity: 0.15, weight: 1 });
    });

    // Highlight selected zone
    if (highlightedZone && zoneLayersRef.current[highlightedZone]) {
      const layer = zoneLayersRef.current[highlightedZone];
      layer.setStyle({ fillOpacity: 0.4, weight: 2 });
      map.fitBounds(layer.getBounds(), {
        padding: [30, 30],
        maxZoom: 16,
        animate: true,
        duration: 0.4,
      });
    }
  }, [highlightedZone, ready]);

  return <div ref={containerRef} className="w-full h-full" />;
};
