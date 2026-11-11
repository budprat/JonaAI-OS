import type { Asset, Finding, RecommendedAction } from "./mock-data";

export interface ExportOptions {
  format: "pdf" | "geojson" | "csv";
  includeFindings: boolean;
  includeActions: boolean;
  includeMap: boolean;
}

export function generateGeoJSON(asset: Asset) {
  // Mock coordinates for each asset
  const coords: Record<string, [number, number]> = {
    northridge: [-118.243, 34.052],
    sunfield: [-117.890, 34.220],
    blackrock: [-119.102, 33.890],
    greendale: [-118.450, 34.180],
    riverview: [-117.650, 34.010],
    crestwood: [-118.010, 34.340],
  };

  const center = coords[asset.id] || [-118.0, 34.0];

  return JSON.stringify(
    {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {
            name: asset.name,
            region: asset.region,
            crop: asset.crop,
            sizeHA: asset.sizeHA,
            healthScore: asset.healthScore,
            status: asset.status,
          },
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [center[0] - 0.02, center[1] - 0.015],
                [center[0] + 0.02, center[1] - 0.015],
                [center[0] + 0.02, center[1] + 0.015],
                [center[0] - 0.02, center[1] + 0.015],
                [center[0] - 0.02, center[1] - 0.015],
              ],
            ],
          },
        },
      ],
    },
    null,
    2
  );
}

export function generateCSV(
  asset: Asset,
  assetFindings: Finding[],
  assetActions: RecommendedAction[]
): string {
  const lines: string[] = [];
  lines.push("Section,Field,Value");
  lines.push(`Asset,Name,${asset.name}`);
  lines.push(`Asset,Region,${asset.region}`);
  lines.push(`Asset,Crop,${asset.crop}`);
  lines.push(`Asset,Size (HA),${asset.sizeHA}`);
  lines.push(`Asset,Health Score,${asset.healthScore}`);
  lines.push(`Asset,Status,${asset.status}`);

  assetFindings.forEach((f, i) => {
    lines.push(`Finding ${i + 1},Severity,${f.severity}`);
    lines.push(`Finding ${i + 1},Text,"${f.text}"`);
  });

  assetActions.forEach((a, i) => {
    lines.push(`Action ${i + 1},Title,"${a.title}"`);
    lines.push(`Action ${i + 1},Target,"${a.target}"`);
    lines.push(`Action ${i + 1},Est. Cost,$${a.estimatedCost}`);
    lines.push(`Action ${i + 1},Priority,${a.priority}`);
  });

  return lines.join("\n");
}

export function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
