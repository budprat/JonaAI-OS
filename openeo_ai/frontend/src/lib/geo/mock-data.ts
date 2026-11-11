// Mock data for the application
export interface Asset {
  id: string;
  name: string;
  region: string;
  crop: string;
  sizeHA: number;
  healthScore: number;
  status: "healthy" | "watch" | "at-risk" | "critical";
  pendingActions: number;
  trend: number[]; // 30 day sparkline data
  yoyChange: number;
}

export interface Finding {
  id: string;
  text: string;
  severity: "critical" | "warning" | "info";
  zone: string;
}

export interface RecommendedAction {
  id: string;
  title: string;
  target: string;
  estimatedCost: number;
  priority: "urgent" | "high" | "medium";
  deployed: boolean;
}

export const assets: Asset[] = [
  {
    id: "northridge",
    name: "Northridge Tract",
    region: "Northern Valley",
    crop: "Corn",
    sizeHA: 1240,
    healthScore: 42,
    status: "at-risk",
    pendingActions: 3,
    trend: [
      72, 70, 68, 65, 63, 60, 58, 55, 53, 50, 52, 49, 47, 45, 44, 43, 42, 42, 43, 42, 41, 42, 43,
      42, 41, 42, 43, 42, 42, 42,
    ],
    yoyChange: -8.2,
  },
  {
    id: "sunfield",
    name: "Sunfield Estate",
    region: "Central Plain",
    crop: "Wheat",
    sizeHA: 890,
    healthScore: 88,
    status: "healthy",
    pendingActions: 0,
    trend: [
      82, 83, 84, 85, 86, 86, 87, 87, 88, 88, 87, 88, 88, 89, 88, 88, 87, 88, 88, 89, 88, 88, 87,
      88, 88, 89, 88, 88, 88, 88,
    ],
    yoyChange: 3.1,
  },
  {
    id: "blackrock",
    name: "Blackrock Fields",
    region: "Southern Basin",
    crop: "Soybean",
    sizeHA: 2100,
    healthScore: 61,
    status: "watch",
    pendingActions: 1,
    trend: [
      75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65, 64, 64, 63, 63, 62, 62, 61, 61, 61, 62, 61, 61,
      61, 62, 61, 61, 61, 61, 61,
    ],
    yoyChange: -4.7,
  },
  {
    id: "greendale",
    name: "Greendale Plots",
    region: "Northern Valley",
    crop: "Corn",
    sizeHA: 560,
    healthScore: 29,
    status: "critical",
    pendingActions: 5,
    trend: [
      65, 62, 58, 55, 50, 48, 45, 42, 40, 38, 36, 35, 34, 33, 32, 31, 30, 30, 29, 29, 30, 29, 29,
      29, 30, 29, 29, 29, 29, 29,
    ],
    yoyChange: -18.4,
  },
  {
    id: "riverview",
    name: "Riverview Acres",
    region: "Eastern Ridge",
    crop: "Rice",
    sizeHA: 780,
    healthScore: 76,
    status: "healthy",
    pendingActions: 0,
    trend: [
      74, 74, 75, 75, 76, 76, 76, 76, 75, 76, 76, 76, 76, 77, 76, 76, 76, 76, 76, 77, 76, 76, 76,
      76, 76, 77, 76, 76, 76, 76,
    ],
    yoyChange: 1.2,
  },
  {
    id: "crestwood",
    name: "Crestwood Farm",
    region: "Central Plain",
    crop: "Barley",
    sizeHA: 430,
    healthScore: 53,
    status: "watch",
    pendingActions: 2,
    trend: [
      68, 67, 66, 65, 64, 63, 62, 61, 60, 59, 58, 57, 56, 56, 55, 55, 54, 54, 53, 53, 54, 53, 53,
      53, 54, 53, 53, 53, 53, 53,
    ],
    yoyChange: -6.1,
  },
];

export const findings: Record<string, Finding[]> = {
  northridge: [
    {
      id: "f1",
      text: "Eastern fields show 14% vegetation stress across 320 HA.",
      severity: "critical",
      zone: "east",
    },
    {
      id: "f2",
      text: "Root cause: suspected irrigation pump 4 failure since Oct 12.",
      severity: "critical",
      zone: "east",
    },
    {
      id: "f3",
      text: "Estimated yield impact at current trajectory: -$45,000.",
      severity: "warning",
      zone: "east",
    },
    {
      id: "f4",
      text: "Western sectors stable. NDVI within seasonal norms.",
      severity: "info",
      zone: "west",
    },
  ],
  greendale: [
    {
      id: "f5",
      text: "Severe nutrient depletion detected across 80% of monitored area.",
      severity: "critical",
      zone: "central",
    },
    {
      id: "f6",
      text: "Soil moisture 34% below threshold in Zones 1-4.",
      severity: "critical",
      zone: "north",
    },
    {
      id: "f7",
      text: "Pest activity (armyworm) confirmed via spectral anomaly.",
      severity: "warning",
      zone: "south",
    },
  ],
  blackrock: [
    {
      id: "f8",
      text: "Mild moisture stress in southern quadrant, 6% below optimal.",
      severity: "warning",
      zone: "south",
    },
    {
      id: "f9",
      text: "No structural anomalies detected. Monitoring continues.",
      severity: "info",
      zone: "central",
    },
  ],
};

export const recommendedActions: Record<string, RecommendedAction[]> = {
  northridge: [
    {
      id: "a1",
      title: "Dispatch maintenance crew to Pump Station 4",
      target: "Zone 3, Eastern Fields",
      estimatedCost: 450,
      priority: "urgent",
      deployed: false,
    },
    {
      id: "a2",
      title: "Deploy supplemental drip irrigation",
      target: "Zone 2-3, Eastern Fields",
      estimatedCost: 2800,
      priority: "high",
      deployed: false,
    },
    {
      id: "a3",
      title: "Schedule aerial nitrogen application",
      target: "Full tract",
      estimatedCost: 1200,
      priority: "medium",
      deployed: false,
    },
  ],
  greendale: [
    {
      id: "a4",
      title: "Emergency soil remediation program",
      target: "Zones 1-4",
      estimatedCost: 8500,
      priority: "urgent",
      deployed: false,
    },
    {
      id: "a5",
      title: "Deploy pest control measures",
      target: "Southern boundary",
      estimatedCost: 3200,
      priority: "urgent",
      deployed: false,
    },
    {
      id: "a6",
      title: "Activate emergency irrigation reserves",
      target: "All zones",
      estimatedCost: 1800,
      priority: "high",
      deployed: false,
    },
  ],
  blackrock: [
    {
      id: "a7",
      title: "Increase irrigation schedule by 15%",
      target: "Southern quadrant",
      estimatedCost: 600,
      priority: "medium",
      deployed: false,
    },
  ],
};

export const contractors = [
  "Valley Ag Services",
  "PrecisionField Co.",
  "Greenline Maintenance",
  "AgriFix Solutions",
];

// ═══════════════════════════════════════════════════════
// AI NARRATIVE ENGINE
// Generates dynamic findings & actions from asset telemetry
// Ported from JonaAI-Intel generateAssetNarrative()
// ═══════════════════════════════════════════════════════

interface CropFactor {
  stressType: string;
  metric: string;
  unit: string;
  threshold: string;
}

const cropFactors: Record<string, CropFactor> = {
  Corn: {
    stressType: "canopy temperature elevation",
    metric: "thermal differential",
    unit: "\u00b0C",
    threshold: "3.0",
  },
  Wheat: {
    stressType: "moisture stress",
    metric: "leaf water potential",
    unit: "MPa",
    threshold: "-1.5",
  },
  Soybean: {
    stressType: "chlorosis patterns",
    metric: "chlorophyll index",
    unit: "CCI",
    threshold: "30",
  },
  Rice: {
    stressType: "water level deficit",
    metric: "paddy depth index",
    unit: "cm",
    threshold: "5.0",
  },
  Barley: {
    stressType: "moisture stress",
    metric: "leaf water potential",
    unit: "MPa",
    threshold: "-1.5",
  },
  Cotton: {
    stressType: "boll development delay",
    metric: "phenological stage index",
    unit: "PSI",
    threshold: "0.7",
  },
};

// Seeded random for deterministic output per asset
function seededRandom(seed: string): () => number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (Math.imul(31, h) + seed.charCodeAt(i)) | 0;
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
    h = Math.imul(h ^ (h >>> 13), 0x45d9f3b);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h % 10000) / 10000;
  };
}

function generateNarrativeFindings(asset: Asset): Finding[] {
  const h = asset.healthScore;
  const rng = seededRandom(asset.id);
  const cf = cropFactors[asset.crop] ?? cropFactors["Wheat"]!;
  const ndviBase = ((h / 100) * 0.45 + 0.3).toFixed(2);
  const ndviDelta = h < 60 ? (-(rng() * 0.12 + 0.05)).toFixed(2) : "+0.02";
  const moisturePct = Math.round(20 + h * 0.5 + (rng() - 0.5) * 10);
  const thermalAnomaly =
    h < 50 ? (1.5 + rng() * 2).toFixed(1) : h < 70 ? (0.5 + rng()).toFixed(1) : "0.0";
  const weeksTrend = Math.floor(rng() * 4) + 2;

  const result: Finding[] = [];
  let idx = 0;

  // Finding 1: Primary health assessment with NDVI
  if (h < 50) {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `Vegetation index critically low at NDVI ${ndviBase} (${ndviDelta} over ${weeksTrend} weeks). ${asset.crop} canopy reflectance indicates widespread ${cf.stressType} across ${Math.round(asset.sizeHA * (1 - h / 100))} HA.`,
      severity: "critical",
      zone: "all",
    });
  } else if (h < 70) {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `NDVI trending downward to ${ndviBase} (${ndviDelta} over ${weeksTrend} weeks). Early-stage ${cf.stressType} detected in ${asset.region.toLowerCase()} sectors. ${cf.metric} approaching threshold of ${cf.threshold} ${cf.unit}.`,
      severity: "warning",
      zone: "east",
    });
  } else {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `Vegetation health nominal at NDVI ${ndviBase}. ${asset.crop} development tracking expected phenological curve. No anomalies detected across ${asset.sizeHA.toLocaleString()} HA.`,
      severity: "info",
      zone: "all",
    });
  }

  // Finding 2: Soil moisture / thermal analysis
  if (h < 60) {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `Soil moisture at ${moisturePct}% capacity — below optimal range for ${asset.crop.toLowerCase()}. Thermal anomaly of +${thermalAnomaly}\u00b0C detected in eastern quadrant, consistent with irrigation deficit or root zone stress.`,
      severity: "critical",
      zone: "east",
    });
  } else if (h < 75) {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `Soil moisture adequate at ${moisturePct}% but declining. Thermal signature shows +${thermalAnomaly}\u00b0C variance in western perimeter — monitor for emerging stress pattern.`,
      severity: "warning",
      zone: "west",
    });
  } else {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `Soil moisture at ${moisturePct}% — within optimal range. Thermal profile uniform across all zones, no hotspots detected.`,
      severity: "info",
      zone: "all",
    });
  }

  // Finding 3: Yield / economic projection
  const yieldImpact = h > 70 ? 0 : -(100 - h) * 600;
  if (yieldImpact < 0) {
    const lossPerHa = Math.round(Math.abs(yieldImpact) / asset.sizeHA);
    const direction = h > 50 ? "Declining" : "Rapidly declining";
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `Projected yield loss of $${Math.abs(yieldImpact).toLocaleString()} ($${lossPerHa}/HA) at current trajectory. ${direction === "Rapidly declining" ? "Without intervention within 14 days, losses may compound 30-40% as crop enters reproductive phase." : "Timely corrective action could recover 40-60% of projected losses."}`,
      severity: yieldImpact < -20000 ? "critical" : "warning",
      zone: "all",
    });
  }

  // Finding 4: Pending items
  if (asset.pendingActions > 0) {
    result.push({
      id: `gen-${asset.id}-${idx++}`,
      text: `${asset.pendingActions} unresolved operational items flagged across ${asset.name}. Deferred maintenance correlates with accelerated health decline in ${asset.region} region assets.`,
      severity: "warning",
      zone: "all",
    });
  }

  return result;
}

function generateNarrativeActions(asset: Asset): RecommendedAction[] {
  const h = asset.healthScore;
  const cf = cropFactors[asset.crop] ?? cropFactors["Wheat"]!;
  const result: RecommendedAction[] = [];
  let idx = 0;

  if (h < 50) {
    result.push({
      id: `gen-a-${asset.id}-${idx++}`,
      title: `Emergency ${cf.stressType.split(" ")[0]} assessment — deploy field sensors`,
      target: `${asset.name} — Full site`,
      estimatedCost: Math.round(asset.sizeHA * 0.4),
      priority: "urgent",
      deployed: false,
    });
    result.push({
      id: `gen-a-${asset.id}-${idx++}`,
      title: "Targeted irrigation adjustment for eastern quadrant",
      target: `${asset.name} — Eastern Fields`,
      estimatedCost: Math.round(asset.sizeHA * 0.2),
      priority: "urgent",
      deployed: false,
    });
  } else if (h < 70) {
    result.push({
      id: `gen-a-${asset.id}-${idx++}`,
      title: `Schedule ${cf.metric} sampling for affected zones`,
      target: `${asset.name} — ${asset.region} Sectors`,
      estimatedCost: Math.round(asset.sizeHA * 0.15),
      priority: "high",
      deployed: false,
    });
  }

  if (asset.pendingActions > 0) {
    result.push({
      id: `gen-a-${asset.id}-${idx++}`,
      title: `Review and resolve ${asset.pendingActions} pending operational items`,
      target: `${asset.name} — Full site`,
      estimatedCost: 500,
      priority: "medium",
      deployed: false,
    });
  }

  if (h < 70) {
    result.push({
      id: `gen-a-${asset.id}-${idx++}`,
      title: "Deploy drone survey for high-resolution damage mapping",
      target: `${asset.name} — Affected zones`,
      estimatedCost: 300,
      priority: "high",
      deployed: false,
    });
  }

  return result;
}

/**
 * Get findings for an asset. Returns hardcoded data if available,
 * otherwise generates dynamic findings from the narrative engine.
 */
export function getAssetFindings(assetId: string): Finding[] {
  if (findings[assetId]?.length) return findings[assetId]!;
  const asset = assets.find((a) => a.id === assetId);
  if (!asset) return [];
  return generateNarrativeFindings(asset);
}

/**
 * Get recommended actions for an asset. Returns hardcoded data if available,
 * otherwise generates dynamic actions from the narrative engine.
 */
export function getAssetActions(assetId: string): RecommendedAction[] {
  if (recommendedActions[assetId]?.length) return recommendedActions[assetId]!;
  const asset = assets.find((a) => a.id === assetId);
  if (!asset) return [];
  return generateNarrativeActions(asset);
}
