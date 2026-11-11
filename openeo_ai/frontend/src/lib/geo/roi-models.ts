export interface InterventionType {
  id: string;
  name: string;
  category: string;
  baseCostPerHA: number;
  expectedRecovery: number;
  paybackWeeks: number;
}

export interface ROIResult {
  totalCost: number;
  projectedRecovery: number;
  netBenefit: number;
  roiPercent: number;
  paybackWeeks: number;
  breakEvenHA: number;
}

export interface Benchmark {
  label: string;
  avgROI: number;
  avgCostPerHA: number;
  successRate: number;
}

export const interventionTypes: InterventionType[] = [
  {
    id: "irrigation_repair",
    name: "Irrigation System Repair",
    category: "Infrastructure",
    baseCostPerHA: 45,
    expectedRecovery: 0.82,
    paybackWeeks: 3,
  },
  {
    id: "pest_control",
    name: "Pest Control Application",
    category: "Chemical",
    baseCostPerHA: 32,
    expectedRecovery: 0.75,
    paybackWeeks: 2,
  },
  {
    id: "soil_remediation",
    name: "Soil Remediation Program",
    category: "Agronomic",
    baseCostPerHA: 85,
    expectedRecovery: 0.68,
    paybackWeeks: 8,
  },
  {
    id: "drainage_improvement",
    name: "Drainage Improvement",
    category: "Infrastructure",
    baseCostPerHA: 120,
    expectedRecovery: 0.91,
    paybackWeeks: 12,
  },
  {
    id: "nutrient_application",
    name: "Targeted Nutrient Application",
    category: "Chemical",
    baseCostPerHA: 28,
    expectedRecovery: 0.79,
    paybackWeeks: 4,
  },
  {
    id: "cover_crop",
    name: "Cover Crop Establishment",
    category: "Agronomic",
    baseCostPerHA: 55,
    expectedRecovery: 0.65,
    paybackWeeks: 16,
  },
];

export const benchmarks: Benchmark[] = [
  { label: "Corn — Midwest", avgROI: 245, avgCostPerHA: 52, successRate: 78 },
  { label: "Wheat — Plains", avgROI: 198, avgCostPerHA: 38, successRate: 82 },
  { label: "Soybean — South", avgROI: 312, avgCostPerHA: 41, successRate: 74 },
  { label: "Rice — Delta", avgROI: 176, avgCostPerHA: 67, successRate: 71 },
  { label: "Industry Average", avgROI: 232, avgCostPerHA: 49, successRate: 76 },
];

export function calculateROI(
  intervention: InterventionType,
  areaHA: number,
  currentHealth: number,
  yieldValuePerHA: number = 1200,
): ROIResult {
  const healthDeficit = (100 - currentHealth) / 100;
  const totalCost = intervention.baseCostPerHA * areaHA;
  const projectedRecovery =
    yieldValuePerHA * areaHA * healthDeficit * intervention.expectedRecovery;
  const netBenefit = projectedRecovery - totalCost;
  const roiPercent = totalCost > 0 ? (netBenefit / totalCost) * 100 : 0;
  const breakEvenHA =
    intervention.baseCostPerHA > 0
      ? totalCost /
        (yieldValuePerHA * healthDeficit * intervention.expectedRecovery)
      : 0;

  return {
    totalCost,
    projectedRecovery,
    netBenefit,
    roiPercent: Math.round(roiPercent),
    paybackWeeks: intervention.paybackWeeks,
    breakEvenHA: Math.round(breakEvenHA),
  };
}

// ═══════════════════════════════════════════════════════
// CLIMATE SCENARIO MODELING (ported from JonaAI-Intel)
// ═══════════════════════════════════════════════════════

export type ScenarioKey = "normal" | "drought" | "frost" | "optimal";

export interface ScenarioConfig {
  label: string;
  lossMultiplier: number;
  effMultiplier: number;
  color: string;
}

export const scenarioConfigs: Record<ScenarioKey, ScenarioConfig> = {
  normal: {
    label: "Normal",
    lossMultiplier: 1.0,
    effMultiplier: 1.0,
    color: "text-muted-foreground",
  },
  drought: {
    label: "Drought",
    lossMultiplier: 1.3,
    effMultiplier: 0.8,
    color: "text-status-warn",
  },
  frost: {
    label: "Frost",
    lossMultiplier: 1.5,
    effMultiplier: 0.7,
    color: "text-status-critical",
  },
  optimal: {
    label: "Optimal",
    lossMultiplier: 0.8,
    effMultiplier: 1.15,
    color: "text-status-good",
  },
};

export interface ScenarioResult {
  recovery: number;
  roi: number;
  breakevenDays: number;
  projectedHealth: number;
  label: string;
  color: string;
}

export interface PresetValues {
  investment: number;
  delay: number;
  effectiveness: number;
}

export const presets: Record<string, PresetValues> = {
  conservative: { investment: 1000, delay: 3, effectiveness: 40 },
  moderate: { investment: 3000, delay: 7, effectiveness: 65 },
  aggressive: { investment: 8000, delay: 1, effectiveness: 90 },
};

export function computeScenarioROI(
  currentHealth: number,
  yieldImpact: number,
  investment: number,
  delay: number,
  effectiveness: number,
  scenarioKey: ScenarioKey,
): ScenarioResult {
  const sc = scenarioConfigs[scenarioKey];
  const absImpact = Math.abs(yieldImpact) * sc.lossMultiplier;
  const effAdj = Math.min(100, effectiveness * sc.effMultiplier);
  const timeFactor = Math.max(0, 1 - delay / 90);
  const recovery = absImpact * (effAdj / 100) * timeFactor;
  const roi = investment > 0 ? ((recovery - investment) / investment) * 100 : 0;
  const breakevenDays =
    recovery > 0 ? Math.ceil(investment / (recovery / 90)) : 0;
  const projectedHealth = Math.min(
    100,
    currentHealth + (100 - currentHealth) * (effAdj / 100) * timeFactor,
  );

  return {
    recovery: Math.round(recovery),
    roi: Math.round(roi),
    breakevenDays,
    projectedHealth: Math.round(projectedHealth),
    label: sc.label,
    color: sc.color,
  };
}
