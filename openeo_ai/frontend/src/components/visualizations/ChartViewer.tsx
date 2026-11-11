import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChartVisualization } from "@/types";
import { cn } from "@/lib/utils";

interface ChartViewerProps {
  data: ChartVisualization;
  className?: string;
}

const COLORS = [
  "#3B82F6", // blue
  "#10B981", // green
  "#F59E0B", // yellow
  "#EF4444", // red
  "#8B5CF6", // purple
  "#EC4899", // pink
  "#06B6D4", // cyan
  "#F97316", // orange
];

const isDateValue = (val: string | number): boolean =>
  typeof val === "string" && /^\d{4}-\d{2}/.test(val);

const formatDateTick = (val: string | number): string => {
  if (isDateValue(val)) {
    const d = new Date(val as string);
    return isNaN(d.getTime())
      ? String(val)
      : d.toLocaleDateString("en", { month: "short", year: "2-digit" });
  }
  return String(val);
};

const formatTooltipValue = (value: number | string) =>
  typeof value === "number" ? value.toFixed(4) : value;

export function ChartViewer({ data, className }: ChartViewerProps) {
  const chartData = data.xAxis.values.map((x, i) => {
    const point: Record<string, string | number> = { x: x.toString() };
    data.series.forEach((series) => {
      point[series.name] = series.values[i] ?? 0;
    });
    return point;
  });

  const hasDateAxis = data.xAxis.values.some(isDateValue);

  const handleDownload = () => {
    // Create CSV content
    const headers = ["x", ...data.series.map((s) => s.name)];
    const rows = chartData.map((point) =>
      [point.x, ...data.series.map((s) => point[s.name])].join(",")
    );
    const csv = [headers.join(","), ...rows].join("\n");

    // Download
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${data.title || "chart"}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const xAxisProps = {
    dataKey: "x" as const,
    tick: { fontSize: 12 },
    tickLine: false,
    axisLine: false,
    tickFormatter: formatDateTick,
    ...(hasDateAxis ? { angle: -45, textAnchor: "end" as const } : {}),
  };

  const yAxisProps = {
    domain: data.yAxis.range || (["auto", "auto"] as const),
    tick: { fontSize: 12 },
    tickLine: false,
    axisLine: false,
  };

  const tooltipProps = {
    contentStyle: {
      backgroundColor: "hsl(var(--background))",
      border: "1px solid hsl(var(--border))",
      borderRadius: "0.5rem",
    },
    formatter: formatTooltipValue,
  };

  const renderChart = () => {
    const commonProps = {
      data: chartData,
      margin: { top: 10, right: 30, left: 0, bottom: hasDateAxis ? 40 : 0 },
    };

    switch (data.chartType) {
      case "area":
        return (
          <AreaChart {...commonProps}>
            <defs>
              {data.series.map((series, index) => (
                <linearGradient
                  key={series.name}
                  id={`gradient-${index}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="5%"
                    stopColor={series.color || COLORS[index % COLORS.length]}
                    stopOpacity={0.8}
                  />
                  <stop
                    offset="95%"
                    stopColor={series.color || COLORS[index % COLORS.length]}
                    stopOpacity={0}
                  />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis {...xAxisProps} />
            <YAxis {...yAxisProps} />
            <Tooltip {...tooltipProps} />
            <Legend />
            {data.series.map((series, index) => (
              <Area
                key={series.name}
                type="monotone"
                dataKey={series.name}
                stroke={series.color || COLORS[index % COLORS.length]}
                fillOpacity={1}
                fill={`url(#gradient-${index})`}
              />
            ))}
          </AreaChart>
        );

      case "bar":
        return (
          <BarChart {...commonProps}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis {...xAxisProps} />
            <YAxis {...yAxisProps} />
            <Tooltip {...tooltipProps} />
            <Legend />
            {data.series.map((series, index) => (
              <Bar
                key={series.name}
                dataKey={series.name}
                fill={series.color || COLORS[index % COLORS.length]}
                radius={[4, 4, 0, 0]}
              />
            ))}
          </BarChart>
        );

      case "scatter":
        return (
          <ScatterChart {...commonProps}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
            <XAxis {...xAxisProps} />
            <YAxis {...yAxisProps} />
            <Tooltip {...tooltipProps} />
            <Legend />
            {data.series.map((series, index) => (
              <Scatter
                key={series.name}
                name={series.name}
                dataKey={series.name}
                fill={series.color || COLORS[index % COLORS.length]}
              />
            ))}
          </ScatterChart>
        );

      case "line":
      default:
        return (
          <LineChart {...commonProps}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis {...xAxisProps} />
            <YAxis {...yAxisProps} />
            <Tooltip {...tooltipProps} />
            <Legend />
            {data.series.map((series, index) => (
              <Line
                key={series.name}
                type="monotone"
                dataKey={series.name}
                stroke={series.color || COLORS[index % COLORS.length]}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            ))}
          </LineChart>
        );
    }
  };

  return (
    <div className={cn("flex flex-col", className)}>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium">{data.title}</h3>
        <Button variant="ghost" size="icon" onClick={handleDownload} className="h-8 w-8">
          <Download className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex-1">
        <ResponsiveContainer width="100%" height="100%">
          {renderChart()}
        </ResponsiveContainer>
      </div>

      <div className="mt-2 flex justify-between text-xs text-muted-foreground">
        <span>{data.xAxis.label}</span>
        <span>{data.yAxis.label}</span>
      </div>
    </div>
  );
}
