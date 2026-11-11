import type { MapVisualization } from "@/types";

export type AnalysisType =
  | "ndvi"
  | "ndwi"
  | "ndbi"
  | "nbr"
  | "bsi"
  | "ndmi"
  | "ndsi"
  | "terrain"
  | "change_detection"
  | "segmentation"
  | "generic";

export interface ResultTypeConfig {
  type: AnalysisType;
  label: string;
  unit: string;
  formatValue: (v: number) => string;
  legendLabels?: { low: string; high: string };
}

function safe(v: number, fn: (n: number) => string): string {
  if (v == null || Number.isNaN(v)) return "—";
  return fn(v);
}

const CONFIGS: Record<AnalysisType, Omit<ResultTypeConfig, "type">> = {
  ndvi: {
    label: "NDVI",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Bare soil / Water", high: "Dense vegetation" },
  },
  ndwi: {
    label: "NDWI",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Dry land", high: "Water" },
  },
  ndbi: {
    label: "NDBI",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Vegetation", high: "Built-up" },
  },
  nbr: {
    label: "NBR",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Severe burn", high: "Healthy vegetation" },
  },
  bsi: {
    label: "BSI",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Vegetated", high: "Bare soil" },
  },
  ndmi: {
    label: "NDMI",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Low moisture", high: "High moisture" },
  },
  ndsi: {
    label: "NDSI",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "No snow", high: "Snow / Ice" },
  },
  terrain: {
    label: "Elevation",
    unit: "m",
    formatValue: (v) => safe(v, (n) => `${Math.round(n)} m`),
    legendLabels: { low: "Low", high: "High" },
  },
  change_detection: {
    label: "Change",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(3)),
    legendLabels: { low: "Decrease", high: "Increase" },
  },
  segmentation: {
    label: "Class",
    unit: "",
    formatValue: (v) => safe(v, (n) => `Class ${Math.round(n)}`),
  },
  generic: {
    label: "Value",
    unit: "",
    formatValue: (v) => safe(v, (n) => n.toFixed(4)),
  },
};

export function detectResultType(
  data?: MapVisualization,
  title?: string,
): ResultTypeConfig {
  const t = (title ?? "").toLowerCase();
  const cmap = (data?.colormap ?? "").toLowerCase();

  let type: AnalysisType = "generic";

  // Check longer index names first to avoid substring collisions (e.g. "gndvi" before "ndvi")
  if (
    /\bgndvi\b|\bndre\b|\bchl_re\b|\bmsavi\b|\barvi\b|\bsavi\b|\blai\b/.test(t)
  ) {
    type = "ndvi";
  } else if (
    /\bndvi\b|vegetation\sindex|\bevi\b/.test(t) ||
    cmap === "ndvi" ||
    (cmap === "rdylgn" && data?.vmin !== undefined && data.vmin < 0)
  ) {
    type = "ndvi";
  } else if (
    /\bmndwi\b|\bndwi\b|water\sindex/.test(t) ||
    (cmap === "coolwarm" && /water/.test(t))
  ) {
    type = "ndwi";
  } else if (/\bndbi\b|built.?up\sindex/.test(t)) {
    type = "ndbi";
  } else if (/\bdnbr\b|\bnbr\b|burn\s(severity|ratio)/.test(t)) {
    type = "nbr";
  } else if (/\bbsi\b|bare\s?soil/.test(t)) {
    type = "bsi";
  } else if (/\bndmi\b|moisture\sindex/.test(t)) {
    type = "ndmi";
  } else if (/\bndsi\b|snow\sindex/.test(t)) {
    type = "ndsi";
  } else if (
    /\bdem\b|\belevation\b|\bterrain\b|\bheight\b|\baltitude\b/.test(t) ||
    cmap === "terrain"
  ) {
    type = "terrain";
  } else if (/\bchange\b|\bdifference\b|\bcompare\b/.test(t)) {
    type = "change_detection";
  } else if (/\bsegment|\bclassif|\bland\s?cover|\bcanopy\b/.test(t)) {
    type = "segmentation";
  }

  return { type, ...CONFIGS[type] };
}
