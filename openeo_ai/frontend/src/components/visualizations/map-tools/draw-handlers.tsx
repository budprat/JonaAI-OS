/**
 * Map drawing tools: bbox and polygon draw handlers (rendered inside
 * MapContainer to use useMap()) plus the geometry helpers they share
 * with MapViewer's area badge. Extracted from MapViewer.tsx
 * (TECH-DEBT-HOTSPOTS.md #5, phase 1 — pure move).
 */
import { useRef, useEffect, useCallback } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { BBox } from "@/types";

/** Approximate polygon area in km² using the Shoelace formula on lat/lng.
 *  Treats latitude degrees as ~111.32 km and longitude degrees as ~111.32 * cos(lat) km.
 *  Accurate enough for UI display at typical AOI sizes. */
export function computePolygonAreaKm2(points: L.LatLng[]): number {
  if (points.length < 3) return 0;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const avgLat = points.reduce((s, p) => s + p.lat, 0) / points.length;
  const kmPerDegLat = 111.32;
  const kmPerDegLng = 111.32 * Math.cos(toRad(avgLat));

  const coords = points.map((p) => ({
    x: p.lng * kmPerDegLng,
    y: p.lat * kmPerDegLat,
  }));

  // Shoelace formula
  let area = 0;
  for (let i = 0; i < coords.length; i++) {
    const j = (i + 1) % coords.length;
    area += coords[i]!.x * coords[j]!.y;
    area -= coords[j]!.x * coords[i]!.y;
  }
  return Math.abs(area) / 2;
}

/** Convert polygon points to a BBox by taking min/max lat/lng */
export function polygonToBbox(points: L.LatLng[]): BBox | null {
  if (points.length < 3) return null;
  let west = Infinity,
    south = Infinity,
    east = -Infinity,
    north = -Infinity;
  for (const p of points) {
    if (p.lng < west) west = p.lng;
    if (p.lng > east) east = p.lng;
    if (p.lat < south) south = p.lat;
    if (p.lat > north) north = p.lat;
  }
  return {
    west: parseFloat(west.toFixed(6)),
    south: parseFloat(south.toFixed(6)),
    east: parseFloat(east.toFixed(6)),
    north: parseFloat(north.toFixed(6)),
  };
}

/** Format area for display: uses m² for small areas, km² otherwise */
export function formatArea(km2: number): string {
  if (km2 < 1) {
    return `${(km2 * 1e6).toFixed(0)} m\u00B2`;
  }
  if (km2 < 100) {
    return `${km2.toFixed(2)} km\u00B2`;
  }
  return `${km2.toFixed(0)} km\u00B2`;
}

// Bbox drawing logic — rendered inside MapContainer to use useMap()
export function BboxDrawHandler({
  isDrawing,
  bbox,
  onBboxChange,
  onDrawEnd,
}: {
  isDrawing: boolean;
  bbox: BBox | null;
  onBboxChange: (bbox: BBox | null) => void;
  onDrawEnd: () => void;
}) {
  const map = useMap();
  const drawingRef = useRef(false);
  const startRef = useRef<L.LatLng | null>(null);
  const previewRef = useRef<L.Rectangle | null>(null);
  const bboxRectRef = useRef<L.Rectangle | null>(null);
  const onBboxChangeRef = useRef(onBboxChange);
  const onDrawEndRef = useRef(onDrawEnd);

  useEffect(() => {
    onBboxChangeRef.current = onBboxChange;
  }, [onBboxChange]);
  useEffect(() => {
    onDrawEndRef.current = onDrawEnd;
  }, [onDrawEnd]);

  // Sync bbox display
  useEffect(() => {
    if (bboxRectRef.current) {
      map.removeLayer(bboxRectRef.current);
      bboxRectRef.current = null;
    }
    if (bbox) {
      bboxRectRef.current = L.rectangle(
        [
          [bbox.south, bbox.west],
          [bbox.north, bbox.east],
        ],
        {
          color: "#3b82f6",
          weight: 2,
          opacity: 0.8,
          fillOpacity: 0.1,
          dashArray: "6 3",
        },
      ).addTo(map);
    }
    return () => {
      if (bboxRectRef.current) {
        map.removeLayer(bboxRectRef.current);
        bboxRectRef.current = null;
      }
    };
  }, [bbox, map]);

  // Drawing mode
  useEffect(() => {
    if (!isDrawing) return;

    map.dragging.disable();
    map.getContainer().style.cursor = "crosshair";

    const onMouseDown = (e: L.LeafletMouseEvent) => {
      drawingRef.current = true;
      startRef.current = e.latlng;
      if (previewRef.current) {
        map.removeLayer(previewRef.current);
        previewRef.current = null;
      }
    };

    const onMouseMove = (e: L.LeafletMouseEvent) => {
      if (!drawingRef.current || !startRef.current) return;
      const bounds = L.latLngBounds(startRef.current, e.latlng);
      if (previewRef.current) {
        previewRef.current.setBounds(bounds);
      } else {
        previewRef.current = L.rectangle(bounds, {
          color: "#3b82f6",
          weight: 2,
          opacity: 0.8,
          fillOpacity: 0.1,
          dashArray: "6 3",
        }).addTo(map);
      }
    };

    const onMouseUp = (e: L.LeafletMouseEvent) => {
      if (!drawingRef.current || !startRef.current) return;
      drawingRef.current = false;

      const bounds = L.latLngBounds(startRef.current, e.latlng);
      const size = map
        .latLngToContainerPoint(bounds.getNorthEast())
        .distanceTo(map.latLngToContainerPoint(bounds.getSouthWest()));

      if (previewRef.current) {
        map.removeLayer(previewRef.current);
        previewRef.current = null;
      }

      if (size > 5) {
        const newBbox = {
          west: parseFloat(bounds.getWest().toFixed(6)),
          south: parseFloat(bounds.getSouth().toFixed(6)),
          east: parseFloat(bounds.getEast().toFixed(6)),
          north: parseFloat(bounds.getNorth().toFixed(6)),
        };
        onBboxChangeRef.current(newBbox);
        map.fitBounds(bounds, { padding: [30, 30], animate: true });
      }

      onDrawEndRef.current();
    };

    map.on("mousedown", onMouseDown);
    map.on("mousemove", onMouseMove);
    map.on("mouseup", onMouseUp);

    return () => {
      map.off("mousedown", onMouseDown);
      map.off("mousemove", onMouseMove);
      map.off("mouseup", onMouseUp);
      map.dragging.enable();
      map.getContainer().style.cursor = "";
      drawingRef.current = false;
      startRef.current = null;
      if (previewRef.current) {
        map.removeLayer(previewRef.current);
        previewRef.current = null;
      }
    };
  }, [isDrawing, map]);

  return null;
}

// Polygon drawing logic — rendered inside MapContainer to use useMap()
// State machine: idle -> drawing (clicking points) -> complete (closed polygon)
export function PolygonDrawHandler({
  isDrawing,
  onBboxChange,
  onDrawEnd,
  onPolygonUpdate,
}: {
  isDrawing: boolean;
  onBboxChange: (bbox: BBox | null) => void;
  onDrawEnd: () => void;
  onPolygonUpdate: (points: L.LatLng[] | null) => void;
}) {
  const map = useMap();
  const pointsRef = useRef<L.LatLng[]>([]);
  const markersRef = useRef<L.CircleMarker[]>([]);
  const polylineRef = useRef<L.Polyline | null>(null);
  const previewLineRef = useRef<L.Polyline | null>(null);
  const polygonRef = useRef<L.Polygon | null>(null);
  const onBboxChangeRef = useRef(onBboxChange);
  const onDrawEndRef = useRef(onDrawEnd);
  const onPolygonUpdateRef = useRef(onPolygonUpdate);

  useEffect(() => {
    onBboxChangeRef.current = onBboxChange;
  }, [onBboxChange]);
  useEffect(() => {
    onDrawEndRef.current = onDrawEnd;
  }, [onDrawEnd]);
  useEffect(() => {
    onPolygonUpdateRef.current = onPolygonUpdate;
  }, [onPolygonUpdate]);

  // Cleanup all drawn layers
  const cleanup = useCallback(() => {
    markersRef.current.forEach((m) => map.removeLayer(m));
    markersRef.current = [];
    if (polylineRef.current) {
      map.removeLayer(polylineRef.current);
      polylineRef.current = null;
    }
    if (previewLineRef.current) {
      map.removeLayer(previewLineRef.current);
      previewLineRef.current = null;
    }
    if (polygonRef.current) {
      map.removeLayer(polygonRef.current);
      polygonRef.current = null;
    }
    pointsRef.current = [];
  }, [map]);

  // Close the polygon and emit bbox
  const closePolygon = useCallback(() => {
    const points = pointsRef.current;
    if (points.length < 3) return;

    // Remove polyline preview, draw final polygon
    if (polylineRef.current) {
      map.removeLayer(polylineRef.current);
      polylineRef.current = null;
    }
    if (previewLineRef.current) {
      map.removeLayer(previewLineRef.current);
      previewLineRef.current = null;
    }
    // Remove vertex markers
    markersRef.current.forEach((m) => map.removeLayer(m));
    markersRef.current = [];

    polygonRef.current = L.polygon(points, {
      color: "#10b981",
      weight: 2,
      opacity: 0.8,
      fillOpacity: 0.15,
      dashArray: "6 3",
    }).addTo(map);

    // Convert to bbox and emit
    const bbox = polygonToBbox(points);
    if (bbox) {
      onBboxChangeRef.current(bbox);
      const bounds = polygonRef.current.getBounds();
      map.fitBounds(bounds, { padding: [30, 30], animate: true });
    }

    onPolygonUpdateRef.current([...points]);
    onDrawEndRef.current();
  }, [map]);

  useEffect(() => {
    if (!isDrawing) {
      // When drawing mode is turned off externally (cancel), clean up
      cleanup();
      onPolygonUpdateRef.current(null);
      return;
    }

    // Enter drawing mode
    map.dragging.disable();
    map.doubleClickZoom.disable();
    map.getContainer().style.cursor = "crosshair";
    pointsRef.current = [];
    onPolygonUpdateRef.current(null);

    const SNAP_RADIUS = 10; // pixels — distance to first point to close polygon

    const onClick = (e: L.LeafletMouseEvent) => {
      const points = pointsRef.current;

      // Check if clicking near first point to close
      if (points.length >= 3) {
        const firstPx = map.latLngToContainerPoint(points[0]!);
        const clickPx = map.latLngToContainerPoint(e.latlng);
        if (firstPx.distanceTo(clickPx) < SNAP_RADIUS) {
          closePolygon();
          return;
        }
      }

      // Add new point
      points.push(e.latlng);

      // Draw vertex marker
      const isFirst = points.length === 1;
      const marker = L.circleMarker(e.latlng, {
        radius: isFirst ? 6 : 4,
        color: isFirst ? "#10b981" : "#3b82f6",
        fillColor: isFirst ? "#10b981" : "#3b82f6",
        fillOpacity: 1,
        weight: isFirst ? 2 : 1,
      }).addTo(map);
      if (isFirst) {
        marker.bindTooltip("Click to close", {
          permanent: false,
          direction: "right",
          className: "text-xs",
        });
      }
      markersRef.current.push(marker);

      // Update polyline
      if (polylineRef.current) {
        polylineRef.current.setLatLngs(points);
      } else if (points.length >= 2) {
        polylineRef.current = L.polyline(points, {
          color: "#3b82f6",
          weight: 2,
          opacity: 0.8,
          dashArray: "6 3",
        }).addTo(map);
      }
    };

    const onMouseMove = (e: L.LeafletMouseEvent) => {
      const points = pointsRef.current;
      if (points.length === 0) return;

      const lastPoint = points[points.length - 1]!;
      if (previewLineRef.current) {
        previewLineRef.current.setLatLngs([lastPoint, e.latlng]);
      } else {
        previewLineRef.current = L.polyline([lastPoint, e.latlng], {
          color: "#3b82f6",
          weight: 1.5,
          opacity: 0.5,
          dashArray: "4 4",
        }).addTo(map);
      }

      // Highlight first marker when close enough to snap-close
      if (points.length >= 3) {
        const firstPx = map.latLngToContainerPoint(points[0]!);
        const cursorPx = map.latLngToContainerPoint(e.latlng);
        const close = firstPx.distanceTo(cursorPx) < SNAP_RADIUS;
        map.getContainer().style.cursor = close ? "pointer" : "crosshair";
        if (markersRef.current[0]) {
          markersRef.current[0].setStyle({
            radius: close ? 8 : 6,
            fillOpacity: close ? 0.8 : 1,
          });
        }
      }
    };

    const onDblClick = (e: L.LeafletMouseEvent) => {
      L.DomEvent.stopPropagation(e.originalEvent);
      L.DomEvent.preventDefault(e.originalEvent);
      if (pointsRef.current.length >= 3) {
        closePolygon();
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        cleanup();
        onPolygonUpdateRef.current(null);
        onDrawEndRef.current();
      }
      // Undo last point with Ctrl+Z / Cmd+Z
      if ((e.ctrlKey || e.metaKey) && e.key === "z") {
        const points = pointsRef.current;
        if (points.length > 0) {
          points.pop();
          const lastMarker = markersRef.current.pop();
          if (lastMarker) map.removeLayer(lastMarker);
          if (polylineRef.current) {
            if (points.length >= 2) {
              polylineRef.current.setLatLngs(points);
            } else {
              map.removeLayer(polylineRef.current);
              polylineRef.current = null;
            }
          }
        }
      }
    };

    map.on("click", onClick);
    map.on("mousemove", onMouseMove);
    map.on("dblclick", onDblClick);
    document.addEventListener("keydown", onKeyDown);

    return () => {
      map.off("click", onClick);
      map.off("mousemove", onMouseMove);
      map.off("dblclick", onDblClick);
      document.removeEventListener("keydown", onKeyDown);
      map.dragging.enable();
      map.doubleClickZoom.enable();
      map.getContainer().style.cursor = "";
      // Clean up preview line only (polygon/markers persist if completed)
      if (previewLineRef.current) {
        map.removeLayer(previewLineRef.current);
        previewLineRef.current = null;
      }
    };
  }, [isDrawing, map, cleanup, closePolygon]);

  return null;
}
