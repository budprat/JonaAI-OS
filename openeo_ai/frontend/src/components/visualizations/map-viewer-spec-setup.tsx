/**
 * Setup file for MapViewer specs.
 *
 * Substitutes Leaflet, react-leaflet, and sub-components that depend on
 * browser-only APIs unavailable in jsdom (Canvas measurement, WebGL).
 * This enables verifying MapViewer's rendering, controls, layer panel,
 * and prop-handling logic without the full map stack.
 */
import React from "react";
import { vi } from "vitest";

/* ---------- Leaflet stand-in ---------- */

const leafletFns = {
  rectangle: vi.fn(() => ({
    addTo: vi.fn(),
    setBounds: vi.fn(),
  })),
  latLngBounds: vi.fn((a: [number, number], b: [number, number]) => ({
    getNorthEast: () => ({
      lat: Math.max(a[0], b[0]),
      lng: Math.max(a[1], b[1]),
    }),
    getSouthWest: () => ({
      lat: Math.min(a[0], b[0]),
      lng: Math.min(a[1], b[1]),
    }),
    getWest: () => Math.min(a[1], b[1]),
    getSouth: () => Math.min(a[0], b[0]),
    getEast: () => Math.max(a[1], b[1]),
    getNorth: () => Math.max(a[0], b[0]),
  })),
  icon: vi.fn(),
  divIcon: vi.fn(),
  DomUtil: { create: vi.fn() },
};

vi.mock("leaflet", () => ({
  default: leafletFns,
  ...leafletFns,
}));

/* ---------- react-leaflet stand-ins ---------- */

const fakeMapInstance = {
  invalidateSize: vi.fn(),
  setView: vi.fn(),
  getContainer: vi.fn(() => document.createElement("div")),
  fitBounds: vi.fn(),
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
  dragging: { disable: vi.fn(), enable: vi.fn() },
  on: vi.fn(),
  off: vi.fn(),
  removeLayer: vi.fn(),
  latLngToContainerPoint: vi.fn(() => ({ distanceTo: vi.fn(() => 10) })),
};

vi.mock("react-leaflet", () => ({
  MapContainer: React.forwardRef(function MapContainerStandIn(
    props: Record<string, unknown>,
    ref: React.ForwardedRef<unknown>
  ) {
    const children = props.children;
    if (typeof ref === "function") {
      ref(fakeMapInstance);
    } else if (ref && typeof ref === "object") {
      ref.current = fakeMapInstance;
    }
    return (
      <div data-testid="map-container" style={props.style as React.CSSProperties}>
        {children as React.ReactNode}
      </div>
    );
  }),
  TileLayer: function TileLayerStandIn(props: Record<string, unknown>) {
    return <div data-testid="tile-layer" data-url={props.url} />;
  },
  ImageOverlay: function ImageOverlayStandIn(props: Record<string, unknown>) {
    return <div data-testid="image-overlay" data-url={props.url} data-opacity={props.opacity} />;
  },
  GeoJSON: function GeoJSONStandIn() {
    return <div data-testid="geojson-layer" />;
  },
  useMap: () => fakeMapInstance,
  useMapEvents: vi.fn(),
}));

/* ---------- PixelInspector stand-in ---------- */
// PixelInspector uses useMap() internally - replace with simple div.
vi.mock("./map-tools/PixelInspector", () => ({
  PixelInspector: function PixelInspectorStandIn(props: Record<string, unknown>) {
    return <div data-testid="pixel-inspector" data-active={String(props.active)} />;
  },
}));
