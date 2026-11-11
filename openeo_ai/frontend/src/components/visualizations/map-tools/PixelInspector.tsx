import { useEffect, useRef } from "react";
import { useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { useAuth } from "@/components/auth/useAuth";

/** Mirrors the subset of ResultTypeConfig used here */
interface ResultTypeConfig {
  type: string;
  label: string;
  formatValue: (v: number) => string;
}

interface PixelInspectorProps {
  active: boolean;
  bounds?: [[number, number], [number, number]];
  vmin?: number;
  vmax?: number;
  source?: string;
  resultConfig: ResultTypeConfig;
}

/** Escape HTML special characters to prevent XSS in popup template literals. */
const esc = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

export function PixelInspector({
  active,
  bounds,
  vmin,
  vmax,
  source,
  resultConfig,
}: PixelInspectorProps) {
  const map = useMap();
  const { accessToken } = useAuth();
  const popupRef = useRef<L.Popup | null>(null);

  useEffect(() => {
    const container = map.getContainer();
    if (active) {
      container.style.cursor = "crosshair";
    } else {
      container.style.cursor = "";
      if (popupRef.current) {
        map.closePopup(popupRef.current);
        popupRef.current = null;
      }
    }
    return () => {
      container.style.cursor = "";
    };
  }, [active, map]);

  useMapEvents({
    async click(e) {
      if (!active || !bounds) return;

      const { lat, lng } = e.latlng;
      const [[south, west], [north, east]] = bounds;

      if (lat < south || lat > north || lng < west || lng > east) return;

      // Show loading popup
      if (popupRef.current) map.closePopup(popupRef.current);

      const loadingContent = `
        <div style="font-family: system-ui; font-size: 12px; line-height: 1.5; min-width: 140px;">
          <div style="font-weight: 600; margin-bottom: 4px; border-bottom: 1px solid #e5e5e5; padding-bottom: 3px;">
            ${esc(resultConfig.label)}
          </div>
          <div><span style="color: #888;">Lat:</span> ${lat.toFixed(5)}</div>
          <div><span style="color: #888;">Lng:</span> ${lng.toFixed(5)}</div>
          <div style="margin-top: 4px; color: #999; font-size: 11px;">Sampling...</div>
        </div>
      `;
      popupRef.current = L.popup({
        closeButton: true,
        className: "pixel-inspector-popup",
      })
        .setLatLng(e.latlng)
        .setContent(loadingContent)
        .openOn(map);

      // Fetch real value from backend
      const value = source
        ? await fetchPixelValue(source, lat, lng, accessToken)
        : fallbackSampleValue(map, e.containerPoint, vmin ?? 0, vmax ?? 1);

      const content = `
        <div style="font-family: system-ui; font-size: 12px; line-height: 1.5; min-width: 140px;">
          <div style="font-weight: 600; margin-bottom: 4px; border-bottom: 1px solid #e5e5e5; padding-bottom: 3px;">
            ${esc(resultConfig.label)}
          </div>
          <div><span style="color: #888;">Lat:</span> ${lat.toFixed(5)}</div>
          <div><span style="color: #888;">Lng:</span> ${lng.toFixed(5)}</div>
          ${
            value !== null
              ? `
            <div style="margin-top: 4px; padding-top: 3px; border-top: 1px solid #e5e5e5;">
              <span style="color: #888;">Value:</span> <strong>${resultConfig.formatValue(value)}</strong>
            </div>
            ${
              getInterpretation(value, resultConfig)
                ? `
              <div style="color: #666; font-size: 11px;">${getInterpretation(value, resultConfig)}</div>
            `
                : ""
            }
          `
              : `
            <div style="margin-top: 4px; color: #999; font-size: 11px;">No data at this location</div>
          `
          }
        </div>
      `;

      if (popupRef.current) {
        popupRef.current.setContent(content);
      }
    },
  });

  return null;
}

async function fetchPixelValue(
  source: string,
  lat: number,
  lng: number,
  authToken: string | null,
): Promise<number | null> {
  try {
    const resp = await fetch(
      `/pixel-value?path=${encodeURIComponent(source)}&lat=${lat}&lng=${lng}`,
      // /pixel-value reads the token from this header only
      { headers: authToken ? { Authorization: `Bearer ${authToken}` } : {} },
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    if (data.nodata) return null;
    return data.value ?? null;
  } catch {
    return null;
  }
}

// Fallback for when source path isn't available (shouldn't normally happen)
function fallbackSampleValue(
  map: L.Map,
  containerPoint: L.Point,
  vmin: number,
  vmax: number,
): number | null {
  try {
    const panes = map.getContainer();
    const images = panes.querySelectorAll<HTMLImageElement>(
      ".leaflet-image-layer",
    );
    if (images.length === 0) return null;
    const img = images[images.length - 1]!;
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth || img.width;
    canvas.height = img.naturalHeight || img.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const imgRect = img.getBoundingClientRect();
    const mapRect = map.getContainer().getBoundingClientRect();
    const relX =
      (containerPoint.x + mapRect.left - imgRect.left) / imgRect.width;
    const relY =
      (containerPoint.y + mapRect.top - imgRect.top) / imgRect.height;
    if (relX < 0 || relX > 1 || relY < 0 || relY > 1) return null;
    const px = Math.floor(relX * canvas.width);
    const py = Math.floor(relY * canvas.height);
    const pixel = ctx.getImageData(px, py, 1, 1).data;
    // Use direct indexing instead of array destructuring to avoid downlevelIteration requirement
    const r = pixel[0]!;
    const g = pixel[1]!;
    const b = pixel[2]!;
    const a = pixel[3]!;
    if (a < 10) return null;
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return vmin + luminance * (vmax - vmin);
  } catch {
    return null;
  }
}

function getInterpretation(
  value: number,
  config: ResultTypeConfig,
): string | null {
  switch (config.type) {
    case "ndvi":
      if (value < -0.1) return "Water";
      if (value < 0.1) return "Bare soil / rock";
      if (value < 0.2) return "Sparse vegetation";
      if (value < 0.4) return "Light vegetation";
      if (value < 0.6) return "Moderate vegetation";
      return "Dense vegetation";

    case "ndwi":
      if (value < -0.3) return "Dry land";
      if (value < 0) return "Low moisture";
      if (value < 0.3) return "Moderate water";
      return "Water body";

    case "nbr":
      if (value < -0.5) return "High severity burn";
      if (value < -0.25) return "Moderate-high burn";
      if (value < -0.1) return "Low-moderate burn";
      if (value < 0.1) return "Unburned";
      if (value < 0.35) return "Low regrowth";
      return "High regrowth";

    case "ndbi":
      if (value < -0.2) return "Dense vegetation";
      if (value < 0) return "Sparse vegetation";
      if (value < 0.2) return "Low built-up";
      return "Built-up area";

    case "bsi":
      if (value < -0.2) return "Dense vegetation";
      if (value < 0) return "Vegetated";
      if (value < 0.2) return "Mixed";
      return "Bare soil";

    case "ndmi":
      if (value < -0.2) return "Dry / stressed";
      if (value < 0) return "Low moisture";
      if (value < 0.2) return "Moderate moisture";
      if (value < 0.4) return "High moisture";
      return "Very high moisture";

    case "ndsi":
      if (value < 0) return "No snow";
      if (value < 0.2) return "Possible snow";
      if (value < 0.4) return "Snow likely";
      return "Snow / ice";

    case "terrain":
      if (value < 0) return "Below sea level";
      if (value < 200) return "Low elevation";
      if (value < 500) return "Moderate elevation";
      if (value < 1500) return "Highland";
      if (value < 3000) return "Mountain";
      return "High mountain";

    case "change_detection":
      if (value < -0.3) return "Significant decrease";
      if (value < -0.1) return "Moderate decrease";
      if (value < 0.1) return "Stable";
      if (value < 0.3) return "Moderate increase";
      return "Significant increase";

    case "segmentation":
      return `Class ${Math.round(value)}`;

    default:
      return null;
  }
}
