export interface ScenarioVariable {
  id: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  baseline: number;
}

export interface ScenarioResult {
  healthProjection: number[];
  yieldDelta: number;
  financialImpact: number;
  riskLevel: "low" | "moderate" | "high" | "critical";
}

export const scenarioVariables: ScenarioVariable[] = [
  { id: "rainfall", label: "Rainfall Change", unit: "%", min: -50, max: 50, step: 5, baseline: 0 },
  { id: "temperature", label: "Temperature Shift", unit: "°C", min: -3, max: 5, step: 0.5, baseline: 0 },
  { id: "intervention_delay", label: "Intervention Delay", unit: "weeks", min: 0, max: 12, step: 1, baseline: 0 },
  { id: "pest_pressure", label: "Pest Pressure", unit: "%", min: 0, max: 100, step: 10, baseline: 20 },
];

export function simulateScenario(
  baselineHealth: number,
  variables: Record<string, number>
): ScenarioResult {
  const rainfall = variables.rainfall ?? 0;
  const temperature = variables.temperature ?? 0;
  const delay = variables.intervention_delay ?? 0;
  const pestPressure = variables.pest_pressure ?? 20;

  // Compute health impact
  let healthModifier = 0;
  healthModifier += rainfall * 0.15; // positive rain helps
  healthModifier -= Math.abs(temperature) * 2.5; // any temp deviation hurts
  healthModifier -= delay * 1.8; // delayed intervention degrades
  healthModifier -= (pestPressure - 20) * 0.12; // above-baseline pest pressure hurts

  const projectedHealth = Math.max(5, Math.min(100, baselineHealth + healthModifier));

  // Generate 12-week trajectory
  const healthProjection: number[] = [];
  for (let w = 0; w < 12; w++) {
    const weekHealth = baselineHealth + ((projectedHealth - baselineHealth) * (w + 1)) / 12;
    healthProjection.push(Math.round(weekHealth * 10) / 10);
  }

  const yieldDelta = Math.round((projectedHealth - baselineHealth) * 12); // $/HA rough
  const financialImpact = yieldDelta * 1000; // portfolio-level

  let riskLevel: ScenarioResult["riskLevel"] = "low";
  if (projectedHealth < 30) riskLevel = "critical";
  else if (projectedHealth < 50) riskLevel = "high";
  else if (projectedHealth < 70) riskLevel = "moderate";

  return { healthProjection, yieldDelta, financialImpact, riskLevel };
}
