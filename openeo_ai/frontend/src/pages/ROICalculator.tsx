import React, { useState, useMemo } from "react";
import {
  interventionTypes,
  benchmarks,
  calculateROI,
  computeScenarioROI,
  scenarioConfigs,
  presets,
  type InterventionType,
  type ScenarioKey,
} from "@/lib/geo/roi-models";
import { Slider } from "@/components/ui/slider";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { Calculator, TrendingUp, DollarSign, Clock } from "lucide-react";

const ROICalculator: React.FC = () => {
  const [selectedIntervention, setSelectedIntervention] = useState<InterventionType>(
    interventionTypes[0]!
  );
  const [areaHA, setAreaHA] = useState(500);
  const [currentHealth, setCurrentHealth] = useState(45);
  const [yieldValue, setYieldValue] = useState(1200);

  // Scenario modeling (ported from JonaAI-Intel)
  const [scenario, setScenario] = useState<ScenarioKey>("normal");
  const [investment, setInvestment] = useState(2500);
  const [delay, setDelay] = useState(7);
  const [effectiveness, setEffectiveness] = useState(70);

  const result = useMemo(
    () => calculateROI(selectedIntervention, areaHA, currentHealth, yieldValue),
    [selectedIntervention, areaHA, currentHealth, yieldValue]
  );

  // Yield impact derived from health deficit
  const yieldImpact = currentHealth > 70 ? 0 : -(100 - currentHealth) * 600;

  const scenarioResult = useMemo(
    () =>
      computeScenarioROI(currentHealth, yieldImpact, investment, delay, effectiveness, scenario),
    [currentHealth, yieldImpact, investment, delay, effectiveness, scenario]
  );

  // All 4 scenarios for comparison table
  const allScenarios = useMemo(
    () =>
      (Object.keys(scenarioConfigs) as ScenarioKey[]).map((key) => ({
        key,
        ...computeScenarioROI(currentHealth, yieldImpact, investment, delay, effectiveness, key),
      })),
    [currentHealth, yieldImpact, investment, delay, effectiveness]
  );

  const chartData = [
    {
      name: "Total Cost",
      value: result.totalCost,
      color: "hsl(var(--status-critical))",
    },
    {
      name: "Projected Recovery",
      value: result.projectedRecovery,
      color: "hsl(var(--status-good))",
    },
    {
      name: "Net Benefit",
      value: Math.max(0, result.netBenefit),
      color: "hsl(var(--primary))",
    },
  ];

  const applyPreset = (name: string) => {
    const p = presets[name];
    if (!p) return;
    setInvestment(p.investment);
    setDelay(p.delay);
    setEffectiveness(p.effectiveness);
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left: Inputs */}
      <div className="w-80 flex-shrink-0 overflow-auto border-r border-border bg-card p-6">
        <div className="mb-6 flex items-center gap-2">
          <Calculator className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            ROI Parameters
          </span>
        </div>

        {/* Intervention Type */}
        <div className="mb-6">
          <label className="mb-2 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Intervention Type
          </label>
          <div className="space-y-1">
            {interventionTypes.map((t) => (
              <button
                key={t.id}
                onClick={() => setSelectedIntervention(t)}
                className={`block w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${
                  selectedIntervention.id === t.id
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-accent"
                }`}
              >
                <span className="block font-medium">{t.name}</span>
                <span className="block font-mono text-xs opacity-70">
                  {t.category} · ${t.baseCostPerHA}/HA
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Area Slider */}
        <div className="mb-6">
          <div className="mb-2 flex justify-between">
            <label className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Area (HA)
            </label>
            <span className="font-mono text-sm font-medium">{areaHA.toLocaleString()}</span>
          </div>
          <Slider
            value={[areaHA]}
            onValueChange={(v) => setAreaHA(v[0] ?? 500)}
            min={50}
            max={5000}
            step={50}
          />
        </div>

        {/* Health Slider */}
        <div className="mb-6">
          <div className="mb-2 flex justify-between">
            <label className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Current Health
            </label>
            <span className="font-mono text-sm font-medium">{currentHealth}/100</span>
          </div>
          <Slider
            value={[currentHealth]}
            onValueChange={(v) => setCurrentHealth(v[0] ?? 45)}
            min={5}
            max={95}
            step={1}
          />
        </div>

        {/* Yield Value */}
        <div className="mb-6">
          <div className="mb-2 flex justify-between">
            <label className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Yield Value ($/HA)
            </label>
            <span className="font-mono text-sm font-medium">${yieldValue.toLocaleString()}</span>
          </div>
          <Slider
            value={[yieldValue]}
            onValueChange={(v) => setYieldValue(v[0] ?? 1200)}
            min={200}
            max={5000}
            step={100}
          />
        </div>

        {/* Divider */}
        <div className="my-6 border-t border-border" />

        {/* Scenario Modeling Section */}
        <div className="mb-4 flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Scenario Modeling
          </span>
        </div>

        {/* Climate Scenario */}
        <div className="mb-5">
          <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Climate Scenario
          </label>
          <select
            value={scenario}
            onChange={(e) => setScenario(e.target.value as ScenarioKey)}
            className="h-10 w-full cursor-pointer rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="normal">Normal Conditions</option>
            <option value="drought">Drought Stress (+30% loss risk)</option>
            <option value="frost">Frost Event (+50% loss risk)</option>
            <option value="optimal">Optimal Season (-20% loss risk)</option>
          </select>
        </div>

        {/* Investment Slider */}
        <div className="mb-5">
          <div className="mb-2 flex justify-between">
            <label className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Investment
            </label>
            <span className="font-mono text-sm font-medium">${investment.toLocaleString()}</span>
          </div>
          <Slider
            value={[investment]}
            onValueChange={(v) => setInvestment(v[0] ?? 2500)}
            min={0}
            max={10000}
            step={100}
          />
        </div>

        {/* Time to Action */}
        <div className="mb-5">
          <div className="mb-2 flex justify-between">
            <label className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Time to Action
            </label>
            <span className="font-mono text-sm font-medium">
              {delay} day{delay !== 1 ? "s" : ""}
            </span>
          </div>
          <Slider
            value={[delay]}
            onValueChange={(v) => setDelay(v[0] ?? 7)}
            min={0}
            max={90}
            step={1}
          />
        </div>

        {/* Effectiveness */}
        <div className="mb-5">
          <div className="mb-2 flex justify-between">
            <label className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Effectiveness
            </label>
            <span className="font-mono text-sm font-medium">{effectiveness}%</span>
          </div>
          <Slider
            value={[effectiveness]}
            onValueChange={(v) => setEffectiveness(v[0] ?? 70)}
            min={0}
            max={100}
            step={5}
          />
        </div>

        {/* Presets */}
        <div className="flex gap-2">
          {Object.keys(presets).map((name) => (
            <button
              key={name}
              onClick={() => applyPreset(name)}
              className="flex-1 border border-border py-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      {/* Right: Results */}
      <div className="flex-1 overflow-auto p-6">
        {/* ROI Header */}
        <div className="mb-8">
          <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Projected Return on Investment
          </span>
          <div className="mt-2 flex items-baseline gap-4">
            <span
              className={`font-mono text-5xl font-semibold ${result.roiPercent >= 0 ? "text-status-good" : "text-status-critical"}`}
            >
              {result.roiPercent}%
            </span>
            <span className="font-mono text-lg text-muted-foreground">ROI</span>
          </div>
        </div>

        {/* Metric Cards */}
        <div className="mb-8 grid grid-cols-4 gap-4">
          <div className="hover-lift border border-border p-4">
            <DollarSign className="mb-2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <span className="block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Total Cost
            </span>
            <span className="mt-1 block font-mono text-xl font-semibold">
              ${result.totalCost.toLocaleString()}
            </span>
          </div>
          <div className="hover-lift border border-border p-4">
            <TrendingUp className="mb-2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <span className="block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Recovery
            </span>
            <span className="text-status-good mt-1 block font-mono text-xl font-semibold">
              ${result.projectedRecovery.toLocaleString()}
            </span>
          </div>
          <div className="hover-lift border border-border p-4">
            <DollarSign className="mb-2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <span className="block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Net Benefit
            </span>
            <span
              className={`mt-1 block font-mono text-xl font-semibold ${result.netBenefit >= 0 ? "text-status-good" : "text-status-critical"}`}
            >
              ${result.netBenefit.toLocaleString()}
            </span>
          </div>
          <div className="hover-lift border border-border p-4">
            <Clock className="mb-2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <span className="block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Payback
            </span>
            <span className="mt-1 block font-mono text-xl font-semibold">
              {result.paybackWeeks} wks
            </span>
          </div>
        </div>

        {/* Chart */}
        <div className="mb-8 border border-border p-4">
          <span className="mb-4 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Cost vs. Benefit Analysis
          </span>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} barSize={60}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis
                dataKey="name"
                tick={{ fontSize: 12, fontFamily: "JetBrains Mono" }}
                stroke="hsl(var(--muted-foreground))"
              />
              <YAxis
                tick={{ fontSize: 12, fontFamily: "JetBrains Mono" }}
                stroke="hsl(var(--muted-foreground))"
                tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
              />
              <Tooltip
                formatter={(value: number) => [`$${value.toLocaleString()}`, ""]}
                contentStyle={{
                  fontFamily: "JetBrains Mono",
                  fontSize: 12,
                  border: "1px solid hsl(var(--border))",
                  borderRadius: 2,
                }}
              />
              <Bar dataKey="value">
                {chartData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Scenario Results (ported from JonaAI-Intel) */}
        <div className="mb-8 border border-border p-5">
          <span className="mb-4 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Scenario Projected Outcomes
          </span>
          <div className="mb-5 grid grid-cols-3 gap-4 text-center">
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase text-muted-foreground">
                Recovery
              </div>
              <div
                className={`font-mono text-xl font-medium ${scenarioResult.recovery > 0 ? "text-status-good" : "text-muted-foreground"}`}
              >
                ${scenarioResult.recovery.toLocaleString()}
              </div>
            </div>
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase text-muted-foreground">ROI</div>
              <div
                className={`font-mono text-xl font-medium ${scenarioResult.roi > 0 ? "text-status-good" : scenarioResult.roi < 0 ? "text-status-critical" : "text-muted-foreground"}`}
              >
                {scenarioResult.roi >= 0 ? "+" : ""}
                {scenarioResult.roi}%
              </div>
            </div>
            <div>
              <div className="mb-1 font-mono text-[10px] uppercase text-muted-foreground">
                Break-even
              </div>
              <div className="font-mono text-xl font-medium">
                {investment === 0
                  ? "0 days"
                  : scenarioResult.breakevenDays > 0 && scenarioResult.breakevenDays < 999
                    ? `${scenarioResult.breakevenDays} days`
                    : "-- days"}
              </div>
            </div>
          </div>

          {/* Health Projection Bars */}
          <div className="mb-5 space-y-2">
            <div className="flex items-center gap-3">
              <span className="w-20 font-mono text-[10px] uppercase text-muted-foreground">
                Current
              </span>
              <div className="relative h-6 flex-1 bg-muted">
                <div
                  className={`h-full transition-all duration-500 ${currentHealth >= 70 ? "bg-status-good" : currentHealth >= 50 ? "bg-status-warn" : "bg-status-critical"}`}
                  style={{ width: `${currentHealth}%` }}
                />
              </div>
              <span className="w-8 text-right font-mono text-sm">{currentHealth}</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="w-20 font-mono text-[10px] uppercase text-muted-foreground">
                Projected
              </span>
              <div className="relative h-6 flex-1 bg-muted">
                <div
                  className={`h-full transition-all duration-500 ${scenarioResult.projectedHealth >= 70 ? "bg-status-good" : scenarioResult.projectedHealth >= 50 ? "bg-status-warn" : "bg-status-critical"}`}
                  style={{ width: `${scenarioResult.projectedHealth}%` }}
                />
              </div>
              <span className="w-8 text-right font-mono text-sm">
                {scenarioResult.projectedHealth}
              </span>
            </div>
          </div>

          {/* Scenario Comparison Table */}
          <div>
            <span className="mb-2 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              All Scenarios Comparison
            </span>
            <div className="space-y-1 font-mono text-sm">
              <div className="flex items-center gap-2 text-[10px] uppercase text-muted-foreground">
                <span className="w-3" />
                <span className="w-16">Scenario</span>
                <span className="w-20 text-right">Recovery</span>
                <span className="w-16 text-right">ROI</span>
                <span className="w-16 text-right">Break-even</span>
              </div>
              {allScenarios.map((s) => (
                <div
                  key={s.key}
                  className={`flex items-center gap-2 ${s.key === scenario ? "font-medium" : "text-muted-foreground"}`}
                >
                  <span className="w-3 text-center">{s.key === scenario ? ">" : ""}</span>
                  <span className={`w-16 ${scenarioConfigs[s.key].color}`}>{s.label}</span>
                  <span className="w-20 text-right">${s.recovery.toLocaleString()}</span>
                  <span
                    className={`w-16 text-right ${s.roi > 0 ? "text-status-good" : s.roi < 0 ? "text-status-critical" : ""}`}
                  >
                    {s.roi >= 0 ? "+" : ""}
                    {s.roi}%
                  </span>
                  <span className="w-16 text-right">
                    {s.breakevenDays > 0 && s.breakevenDays < 999 ? `${s.breakevenDays}d` : "--"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Benchmarks Table */}
        <div className="border border-border">
          <div className="border-b border-border px-4 py-3">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Industry Benchmarks
            </span>
          </div>
          <table className="w-full">
            <thead>
              <tr className="border-b border-border">
                <th className="px-4 py-2 text-left font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  Segment
                </th>
                <th className="px-4 py-2 text-right font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  Avg ROI
                </th>
                <th className="px-4 py-2 text-right font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  Avg Cost/HA
                </th>
                <th className="px-4 py-2 text-right font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  Success Rate
                </th>
              </tr>
            </thead>
            <tbody>
              {benchmarks.map((b) => (
                <tr
                  key={b.label}
                  className="border-b border-border last:border-b-0 hover:bg-accent/50"
                >
                  <td className="px-4 py-2.5 text-sm">{b.label}</td>
                  <td className="text-status-good px-4 py-2.5 text-right font-mono text-sm">
                    {b.avgROI}%
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm">${b.avgCostPerHA}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm">{b.successRate}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default ROICalculator;
