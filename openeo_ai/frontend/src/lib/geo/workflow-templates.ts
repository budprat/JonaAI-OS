export type NodeType = "trigger" | "condition" | "action";

export interface WorkflowNode {
  id: string;
  type: NodeType;
  label: string;
  description: string;
  config: Record<string, string | number>;
}

export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  category: "monitoring" | "alerts" | "analysis" | "reporting";
  keywords: string[];
  nodes: WorkflowNode[];
}

export const workflowTemplates: WorkflowTemplate[] = [
  {
    id: "drought-monitor",
    name: "Drought Monitoring Alert",
    description: "Triggers when soil moisture drops below threshold across monitored zones.",
    category: "alerts",
    keywords: ["drought", "moisture", "dry", "water", "soil"],
    nodes: [
      { id: "n1", type: "trigger", label: "Satellite Pass", description: "New imagery received for monitored zone", config: { frequency: "daily" } },
      { id: "n2", type: "condition", label: "Moisture < 30%", description: "Check soil moisture index against threshold", config: { metric: "moisture", operator: "<", value: 30 } },
      { id: "n3", type: "action", label: "Send Alert", description: "Notify operations team via email and dashboard", config: { channel: "email+dashboard" } },
    ],
  },
  {
    id: "ndvi-decline",
    name: "NDVI Decline Detection",
    description: "Alerts when NDVI drops below 0.3 in any sector, indicating vegetation stress.",
    category: "monitoring",
    keywords: ["ndvi", "vegetation", "stress", "decline", "health", "drop"],
    nodes: [
      { id: "n4", type: "trigger", label: "Weekly NDVI Scan", description: "Compute NDVI from latest multispectral imagery", config: { frequency: "weekly" } },
      { id: "n5", type: "condition", label: "NDVI < 0.3", description: "Flag zones with NDVI below critical threshold", config: { metric: "ndvi", operator: "<", value: 0.3 } },
      { id: "n6", type: "action", label: "Flag Asset", description: "Mark asset as at-risk and queue for briefing", config: { action: "flag_at_risk" } },
    ],
  },
  {
    id: "pest-detection",
    name: "Pest Activity Detection",
    description: "Identifies spectral anomalies consistent with pest infestation patterns.",
    category: "analysis",
    keywords: ["pest", "insect", "armyworm", "infestation", "spectral"],
    nodes: [
      { id: "n7", type: "trigger", label: "Bi-Weekly Analysis", description: "Run spectral anomaly detection algorithm", config: { frequency: "bi-weekly" } },
      { id: "n8", type: "condition", label: "Anomaly Score > 0.7", description: "Check if spectral signature matches pest patterns", config: { metric: "anomaly_score", operator: ">", value: 0.7 } },
      { id: "n9", type: "action", label: "Dispatch Scout", description: "Auto-schedule field scout for verification", config: { action: "dispatch_scout" } },
    ],
  },
  {
    id: "yield-forecast",
    name: "Yield Forecasting Pipeline",
    description: "Generates monthly yield projections based on historical NDVI trends and weather data.",
    category: "reporting",
    keywords: ["yield", "forecast", "projection", "harvest", "production"],
    nodes: [
      { id: "n10", type: "trigger", label: "Monthly Aggregation", description: "Collect 30-day NDVI and weather data", config: { frequency: "monthly" } },
      { id: "n11", type: "condition", label: "Data Completeness > 85%", description: "Ensure sufficient cloud-free imagery for accuracy", config: { metric: "completeness", operator: ">", value: 85 } },
      { id: "n12", type: "action", label: "Generate Report", description: "Compute yield estimate and distribute to stakeholders", config: { action: "generate_yield_report" } },
    ],
  },
  {
    id: "frost-risk",
    name: "Frost Risk Early Warning",
    description: "Monitors thermal bands for frost risk conditions in vulnerable sectors.",
    category: "alerts",
    keywords: ["frost", "cold", "temperature", "thermal", "freeze", "winter"],
    nodes: [
      { id: "n13", type: "trigger", label: "Thermal Monitoring", description: "Continuous thermal band analysis", config: { frequency: "6-hourly" } },
      { id: "n14", type: "condition", label: "Temp < 2°C", description: "Surface temperature below frost threshold", config: { metric: "surface_temp", operator: "<", value: 2 } },
      { id: "n15", type: "action", label: "Emergency Alert", description: "Push urgent notification and activate frost protocol", config: { action: "emergency_alert" } },
    ],
  },
];

export function matchTemplate(query: string): WorkflowTemplate | null {
  const lower = query.toLowerCase();
  let bestMatch: WorkflowTemplate | null = null;
  let bestScore = 0;
  for (const t of workflowTemplates) {
    const score = t.keywords.filter((k) => lower.includes(k)).length;
    if (score > bestScore) {
      bestScore = score;
      bestMatch = t;
    }
  }
  return bestMatch;
}
