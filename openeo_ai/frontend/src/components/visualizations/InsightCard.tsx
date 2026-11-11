// InsightCard — Universal domain-adaptive assessment card
// Renders any domain: vegetation, water, fire, urban, snow, drought, coastal

interface InsightData {
  health_score: number;
  color: "green" | "yellow" | "red";
  assessment: string;
  action: string;
  domain: string;
  question: string;
  index_name: string;
  index_value: number;
}

interface InsightCardProps {
  data: InsightData;
}

const DOMAIN_LABELS: Record<string, { label: string; emoji: string }> = {
  vegetation_health: { label: "Vegetation Health", emoji: "🌿" },
  water_risk: { label: "Water Risk", emoji: "💧" },
  fire_damage: { label: "Fire Damage", emoji: "🔥" },
  urban_expansion: { label: "Urban Expansion", emoji: "🏗️" },
  snow_water: { label: "Snow / Water Supply", emoji: "❄️" },
  moisture_stress: { label: "Moisture Stress", emoji: "🌾" },
};

const COLOR_MAP = {
  green: { bg: "bg-emerald-50 dark:bg-emerald-950/30", text: "text-emerald-700 dark:text-emerald-400", ring: "stroke-emerald-500", badge: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200" },
  yellow: { bg: "bg-amber-50 dark:bg-amber-950/30", text: "text-amber-700 dark:text-amber-400", ring: "stroke-amber-500", badge: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200" },
  red: { bg: "bg-red-50 dark:bg-red-950/30", text: "text-red-700 dark:text-red-400", ring: "stroke-red-500", badge: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200" },
};

export function InsightCard({ data }: InsightCardProps) {
  const fallbackColor = COLOR_MAP.yellow;
  const colors = COLOR_MAP[data.color] || fallbackColor;
  const domain = DOMAIN_LABELS[data.domain] || { label: data.domain, emoji: "📊" };

  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const progress = (data.health_score / 100) * circumference;

  return (
    <div className={`rounded-xl border p-4 shadow-md ${colors.bg}`}>
      <div className="flex items-start gap-4">
        {/* Score ring */}
        <div className="relative w-24 h-24 flex-shrink-0">
          <svg className="w-full h-full -rotate-90" viewBox="0 0 96 96">
            <circle cx="48" cy="48" r={radius} fill="none" strokeWidth="6" className="stroke-gray-200 dark:stroke-gray-700" />
            <circle cx="48" cy="48" r={radius} fill="none" strokeWidth="6" strokeLinecap="round" className={colors.ring}
              strokeDasharray={circumference} strokeDashoffset={circumference - progress}
              style={{ transition: "stroke-dashoffset 0.8s ease-out" }} />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className={`text-xl font-bold ${colors.text}`}>{data.health_score}</span>
            <span className="text-[9px] text-muted-foreground">/100</span>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm">{domain.emoji}</span>
            <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{domain.label}</span>
          </div>
          <p className={`text-base font-bold ${colors.text} leading-tight`}>{data.assessment}</p>
          <p className="text-sm text-muted-foreground mt-1">{data.action}</p>
          <div className="flex items-center gap-2 mt-2">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${colors.badge}`}>
              {data.index_name}: {data.index_value}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export type { InsightData };
