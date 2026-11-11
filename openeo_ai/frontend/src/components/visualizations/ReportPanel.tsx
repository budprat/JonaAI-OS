// ReportPanel — Rich markdown display for analysis reports, interpretations, and Earth-Agent findings.
// Replaces the Stats tab in VisualizationPanel.

import { useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Brain,
  Scan,
  Download,
  Clock,
  BarChart3,
  ShieldCheck,
  FlaskConical,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { ReportSection } from "@/types";

// ---------------------------------------------------------------------------
// Markdown component overrides (report-optimized styling)
// ---------------------------------------------------------------------------

const reportMarkdownComponents: Partial<Components> = {
  h1({ children }) {
    return (
      <h1 className="mb-3 mt-1 border-b pb-2 text-lg font-bold text-foreground first:mt-0">
        {children}
      </h1>
    );
  },
  h2({ children }) {
    return (
      <h2 className="mb-2 mt-4 text-base font-semibold text-foreground first:mt-0">{children}</h2>
    );
  },
  h3({ children }) {
    return (
      <h3 className="mb-1.5 mt-3 text-sm font-semibold text-foreground/90 first:mt-0">
        {children}
      </h3>
    );
  },
  p({ children }) {
    return <p className="mb-2 text-sm leading-relaxed text-foreground/80 last:mb-0">{children}</p>;
  },
  ul({ children }) {
    return <ul className="mb-2 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="mb-2 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>;
  },
  li({ children }) {
    return <li className="text-sm leading-relaxed text-foreground/80">{children}</li>;
  },
  strong({ children }) {
    return <strong className="font-semibold text-foreground">{children}</strong>;
  },
  em({ children }) {
    return <em className="text-foreground/70">{children}</em>;
  },
  code({ className, children }) {
    const isBlock = className?.includes("language-");
    if (isBlock) {
      return (
        <pre className="mb-2 overflow-x-auto rounded-md bg-muted p-3 text-xs">
          <code className="font-mono">{children}</code>
        </pre>
      );
    }
    return (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
        {children}
      </code>
    );
  },
  blockquote({ children }) {
    return (
      <blockquote className="border-l-3 mb-2 border-primary/40 pl-3 text-sm italic text-muted-foreground">
        {children}
      </blockquote>
    );
  },
  table({ children }) {
    return (
      <div className="mb-3 overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">{children}</table>
      </div>
    );
  },
  thead({ children }) {
    return <thead className="bg-muted/50 text-xs font-medium">{children}</thead>;
  },
  th({ children }) {
    return (
      <th className="px-3 py-1.5 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {children}
      </th>
    );
  },
  td({ children }) {
    return <td className="border-t px-3 py-1.5 text-sm tabular-nums">{children}</td>;
  },
  hr() {
    return <hr className="my-3 border-border" />;
  },
  a({ href, children }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline hover:text-primary/80"
      >
        {children}
      </a>
    );
  },
};

// ---------------------------------------------------------------------------
// Config per section type
// ---------------------------------------------------------------------------

interface ReportPanelProps {
  sections: ReportSection[];
}

const SECTION_CONFIG: Record<
  ReportSection["type"],
  { icon: typeof FileText; label: string; color: string; borderColor: string }
> = {
  report: {
    icon: FileText,
    label: "Report",
    color: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    borderColor: "border-l-blue-500",
  },
  interpretation: {
    icon: Brain,
    label: "Interpretation",
    color: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
    borderColor: "border-l-purple-500",
  },
  earth_agent: {
    icon: Scan,
    label: "Earth-Agent",
    color: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
    borderColor: "border-l-emerald-500",
  },
  statistics: {
    icon: BarChart3,
    label: "Statistics",
    color: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
    borderColor: "border-l-amber-500",
  },
  quality: {
    icon: ShieldCheck,
    label: "Quality",
    color: "bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200",
    borderColor: "border-l-gray-500",
  },
  method_card: {
    icon: FlaskConical,
    label: "Method Card",
    color: "bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-200",
    borderColor: "border-l-teal-500",
  },
};

// ---------------------------------------------------------------------------
// Content extraction
// ---------------------------------------------------------------------------

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Strip trailing JSON blobs from markdown content.
 *  The backend may concatenate formatted text + raw JSON in a single string.
 *  Detect the boundary (markdown followed by a JSON object) and strip the JSON.
 */
function stripTrailingJson(text: string): string {
  // Look for a JSON object starting on its own line after markdown content
  const jsonStart = text.search(/\n\s*\{[\s\S]*"interpretation"[\s\S]*\}\s*$/);
  if (jsonStart > 50) {
    // Verify the trailing part is valid JSON
    const candidate = text.slice(jsonStart).trim();
    try {
      JSON.parse(candidate);
      return text.slice(0, jsonStart).trim();
    } catch {
      // Not valid JSON — keep as-is
    }
  }
  return text;
}

/** Extract readable markdown from potentially JSON-wrapped content */
function extractMarkdown(content: string): string {
  try {
    const parsed = JSON.parse(content);
    if (typeof parsed === "object" && parsed !== null) {
      // Direct text fields (markdown content)
      for (const key of ["report", "text", "summary", "content", "markdown"]) {
        if (typeof parsed[key] === "string") return parsed[key];
      }

      // interpret_result returns structured data — format it as markdown
      if (parsed.interpretation || parsed.statistics) {
        return formatInterpretationAsMarkdown(parsed);
      }

      // Array of {type, text} blocks (Earth-Agent)
      if (Array.isArray(parsed)) {
        const texts = parsed
          .filter((item: Record<string, unknown>) => typeof item?.text === "string")
          .map((item: Record<string, unknown>) => item.text as string);
        if (texts.length > 0) return texts.join("\n\n");
      }

      // Fallback: format the object as a readable list
      return formatObjectAsMarkdown(parsed);
    }
  } catch {
    // Not JSON — use as-is (already markdown or plain text)
    // Safety: strip any trailing JSON blob that was concatenated
    return stripTrailingJson(content);
  }
  return content;
}

/** Convert interpret_result JSON into rich readable markdown.
 *  Fallback for when the backend sends structured JSON instead of pre-formatted text.
 */
function formatInterpretationAsMarkdown(data: Record<string, unknown>): string {
  const lines: string[] = [];

  const interp = data.interpretation as Record<string, unknown> | undefined;
  const stats = data.statistics as Record<string, unknown> | undefined;
  const quality = data.quality as Record<string, unknown> | undefined;

  // --- Header ---
  if (interp) {
    const index = interp.index || "Unknown";
    const domain = ((interp.domain as string) || "unknown").replace(/_/g, " ");
    const grade = interp.grade || "?";
    const score = interp.grade_score ?? "?";
    lines.push(`## ${index} Interpretation — Grade: ${grade} (${score}/100)`);
    lines.push("");

    // Executive Summary
    lines.push("### Executive Summary");
    lines.push("");
    const meanCat = interp.mean_category as string;
    const summaryText = interp.summary as string;
    if (summaryText) {
      lines.push(summaryText);
    } else {
      lines.push(
        `The ${domain} analysis using **${index}** received a grade of **${grade}** (${score}/100).`
      );
    }
    if (stats) {
      const innerStats = (stats.statistics ?? stats) as Record<string, unknown>;
      const meanVal = innerStats.mean as number | undefined;
      if (meanVal !== undefined && meanCat) {
        lines.push(
          ` The mean value of **${meanVal.toFixed(4)}** falls within the **${meanCat}** category.`
        );
      }
    }
    lines.push("");

    // Key Findings
    lines.push("### Key Findings");
    lines.push("");
    lines.push(`- **Overall Grade**: ${grade} (${score}/100)`);
    if (stats) {
      const innerStats = (stats.statistics ?? stats) as Record<string, unknown>;
      const meanVal = innerStats.mean as number | undefined;
      if (meanVal !== undefined) {
        lines.push(`- **Mean ${index}**: ${meanVal.toFixed(4)} (${meanCat || "Unclassified"})`);
      }
      const stdVal = innerStats.std as number | undefined;
      if (stdVal !== undefined) {
        const variability = stdVal < 0.05 ? "low" : stdVal < 0.15 ? "moderate" : "high";
        lines.push(`- **Spatial Variability**: ${variability} (std: ${stdVal.toFixed(4)})`);
      }
    }
    const coveragePct = stats?.coverage_pct as number | undefined;
    if (coveragePct !== undefined) {
      lines.push(`- **Data Coverage**: ${coveragePct}% valid pixels`);
    }
    lines.push("");

    // Category Breakdown
    const breakdown = interp.category_breakdown as Array<Record<string, unknown>> | undefined;
    if (breakdown && breakdown.length > 0) {
      lines.push("### Category Breakdown");
      lines.push("");
      lines.push("| Category | Range | Est. Area | Assessment |");
      lines.push("|----------|-------|-----------|------------|");
      for (const cat of breakdown) {
        const pct = cat.est_area_pct != null ? `~${cat.est_area_pct}%` : "N/A";
        lines.push(`| ${cat.category} | ${cat.range} | ${pct} | ${cat.assessment} |`);
      }
      lines.push("");
    }
  }

  // Statistics Table
  if (stats) {
    const innerStats = (stats.statistics ?? stats) as Record<string, unknown>;
    const statKeys: [string, string][] = [
      ["min", "Minimum"],
      ["max", "Maximum"],
      ["mean", "Mean"],
      ["median", "Median"],
      ["std", "Std Deviation"],
      ["p5", "5th Percentile"],
      ["p25", "25th Percentile"],
      ["p75", "75th Percentile"],
      ["p95", "95th Percentile"],
    ];
    const hasStats = statKeys.some(([k]) => innerStats[k] != null);
    if (hasStats) {
      lines.push("### Statistics");
      lines.push("");
      lines.push("| Metric | Value |");
      lines.push("|--------|-------|");
      for (const [key, label] of statKeys) {
        const val = innerStats[key];
        if (val != null && typeof val === "number") {
          lines.push(`| ${label} | ${val.toFixed(6)} |`);
        }
      }
      lines.push("");
    }
  }

  // Quality Assessment
  if (quality) {
    const qGrade = quality.grade || "?";
    const qScore = quality.score ?? "?";
    lines.push("### Data Quality");
    lines.push("");
    lines.push(`**Grade: ${qGrade} (${qScore}/100)**`);
    const flags = quality.flags as string[] | undefined;
    if (flags && flags.length > 0) {
      lines.push("");
      for (const f of flags) lines.push(`- ${f}`);
    } else {
      lines.push("");
      lines.push("- No quality issues detected");
    }
    lines.push("");
  }

  // Warnings
  if (interp) {
    const warnings = interp.warnings as string[] | undefined;
    if (warnings && warnings.length > 0) {
      lines.push("### Warnings");
      lines.push("");
      for (const w of warnings) lines.push(`- ${w}`);
      lines.push("");
    }

    // Recommendations
    const tips = interp.tips as string[] | undefined;
    if (tips && tips.length > 0) {
      lines.push("### Recommendations");
      lines.push("");
      for (const t of tips) lines.push(`- ${t}`);
      lines.push("");
    }
  }

  return lines.join("\n");
}

/** Format a generic object as a readable markdown list */
function formatObjectAsMarkdown(obj: Record<string, unknown>, depth = 0): string {
  const indent = "  ".repeat(depth);
  const lines: string[] = [];
  for (const [key, val] of Object.entries(obj)) {
    if (val === null || val === undefined) continue;
    if (typeof val === "object" && !Array.isArray(val)) {
      lines.push(`${indent}- **${key}**:`);
      lines.push(formatObjectAsMarkdown(val as Record<string, unknown>, depth + 1));
    } else if (Array.isArray(val)) {
      lines.push(`${indent}- **${key}**: ${val.join(", ")}`);
    } else {
      const formatted = typeof val === "number" ? val.toFixed(4) : String(val);
      lines.push(`${indent}- **${key}**: ${formatted}`);
    }
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Section Card
// ---------------------------------------------------------------------------

function SectionCard({ section }: { section: ReportSection }) {
  const [expanded, setExpanded] = useState(true);
  const config = SECTION_CONFIG[section.type] || SECTION_CONFIG.report;
  const Icon = config.icon;
  const markdown = extractMarkdown(section.content);

  return (
    <Card className={`overflow-hidden border-l-4 ${config.borderColor}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        )}
        <Icon className="h-4 w-4 flex-shrink-0" />
        <span className="flex-1 truncate text-sm font-medium">{section.title}</span>
        <Badge variant="outline" className={`px-1.5 py-0 text-[10px] ${config.color}`}>
          {config.label}
        </Badge>
        <span className="flex flex-shrink-0 items-center gap-1 text-[10px] text-muted-foreground">
          <Clock className="h-2.5 w-2.5" />
          {formatTime(section.timestamp)}
        </span>
      </button>
      {expanded && (
        <CardContent className="px-4 pb-4 pt-0">
          <div className="max-h-[500px] overflow-auto rounded-md bg-background p-4">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={reportMarkdownComponents}>
              {markdown}
            </ReactMarkdown>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Report Panel
// ---------------------------------------------------------------------------

export function ReportPanel({ sections }: ReportPanelProps) {
  if (sections.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
        <div className="rounded-full bg-muted p-4">
          <FileText className="h-8 w-8 opacity-40" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium">No reports yet</p>
          <p className="mt-1 text-xs opacity-60">
            Analysis reports, interpretations, and Earth-Agent findings will appear here
          </p>
        </div>
      </div>
    );
  }

  const handleExport = () => {
    const markdown = sections
      .map((s) => {
        const content = extractMarkdown(s.content);
        return `## ${s.title}\n\n*${s.type} | ${formatTime(s.timestamp)} | ${s.toolName}*\n\n${content}\n`;
      })
      .join("\n---\n\n");

    const blob = new Blob([`# Analysis Report\n\n${markdown}`], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `analysis-report-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full flex-col gap-3 overflow-auto">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">
            {sections.length} section{sections.length !== 1 ? "s" : ""}
          </span>
          <div className="flex gap-1">
            {Array.from(new Set(sections.map((s) => s.type))).map((type) => {
              const cfg = SECTION_CONFIG[type] || SECTION_CONFIG.report;
              return (
                <Badge key={type} variant="outline" className={`px-1 py-0 text-[9px] ${cfg.color}`}>
                  {cfg.label}
                </Badge>
              );
            })}
          </div>
        </div>
        <button
          onClick={handleExport}
          className="flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Download className="h-3 w-3" />
          Export MD
        </button>
      </div>
      <div className="flex flex-col gap-2">
        {sections.map((section) => (
          <SectionCard key={section.id} section={section} />
        ))}
      </div>
    </div>
  );
}
