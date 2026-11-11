import React, { useState } from "react";
import { assets, getAssetFindings, getAssetActions } from "@/lib/geo/mock-data";
import { generateGeoJSON, generateCSV, downloadFile } from "@/lib/geo/export-utils";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { FileDown, FileJson, FileText, Loader2 } from "lucide-react";

interface ExportPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  assetId: string;
}

export const ExportPanel: React.FC<ExportPanelProps> = ({ open, onOpenChange, assetId }) => {
  const [format, setFormat] = useState<"pdf" | "geojson" | "csv">("pdf");
  const [includeFindings, setIncludeFindings] = useState(true);
  const [includeActions, setIncludeActions] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);

  const asset = assets.find((a) => a.id === assetId);
  const assetFindings = getAssetFindings(assetId);
  const assetActions = getAssetActions(assetId);

  if (!asset) return null;

  const handleExport = async () => {
    setIsGenerating(true);

    // Simulate processing delay
    await new Promise((r) => setTimeout(r, 1200));

    if (format === "geojson") {
      const geojson = generateGeoJSON(asset);
      downloadFile(geojson, `${asset.id}-export.geojson`, "application/geo+json");
    } else if (format === "csv") {
      const csv = generateCSV(
        asset,
        includeFindings ? assetFindings : [],
        includeActions ? assetActions : []
      );
      downloadFile(csv, `${asset.id}-report.csv`, "text/csv");
    } else {
      // PDF: generate a simple text-based report
      const lines: string[] = [];
      lines.push(`EXECUTIVE BRIEFING — ${asset.name}`);
      lines.push(`${"=".repeat(50)}`);
      lines.push(`Region: ${asset.region}`);
      lines.push(`Crop: ${asset.crop}`);
      lines.push(`Area: ${asset.sizeHA.toLocaleString()} HA`);
      lines.push(`Health Score: ${asset.healthScore}/100`);
      lines.push(`Status: ${asset.status}`);
      lines.push("");

      if (includeFindings && assetFindings.length > 0) {
        lines.push("FINDINGS");
        lines.push("-".repeat(30));
        assetFindings.forEach((f, i) => {
          lines.push(`${i + 1}. [${f.severity.toUpperCase()}] ${f.text}`);
        });
        lines.push("");
      }

      if (includeActions && assetActions.length > 0) {
        lines.push("RECOMMENDED ACTIONS");
        lines.push("-".repeat(30));
        assetActions.forEach((a, i) => {
          lines.push(`${i + 1}. ${a.title}`);
          lines.push(
            `   Target: ${a.target} | Est. Cost: $${a.estimatedCost} | Priority: ${a.priority}`
          );
        });
      }

      const content = lines.join("\n");
      downloadFile(content, `${asset.id}-briefing.txt`, "text/plain");
    }

    setIsGenerating(false);
    onOpenChange(false);
  };

  const formats = [
    {
      id: "pdf" as const,
      label: "PDF Report",
      icon: FileText,
      desc: "Executive summary document",
    },
    {
      id: "geojson" as const,
      label: "GeoJSON",
      icon: FileJson,
      desc: "Geospatial data with boundaries",
    },
    {
      id: "csv" as const,
      label: "CSV",
      icon: FileDown,
      desc: "Tabular data export",
    },
  ];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-96">
        <SheetHeader>
          <SheetTitle>Export Report</SheetTitle>
          <SheetDescription>
            {asset.name} — {asset.region}
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          {/* Format Selection */}
          <div>
            <span className="mb-3 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Format
            </span>
            <div className="space-y-2">
              {formats.map((f) => {
                const Icon = f.icon;
                return (
                  <button
                    key={f.id}
                    onClick={() => setFormat(f.id)}
                    className={`flex w-full items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
                      format === f.id
                        ? "border-primary bg-accent"
                        : "border-border hover:bg-accent/50"
                    }`}
                  >
                    <Icon className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <span className="block text-sm font-medium">{f.label}</span>
                      <span className="text-xs text-muted-foreground">{f.desc}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Content Options */}
          <div>
            <span className="mb-3 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Include
            </span>
            <div className="space-y-3">
              <label className="flex cursor-pointer items-center gap-3">
                <Checkbox
                  checked={includeFindings}
                  onCheckedChange={(c) => setIncludeFindings(!!c)}
                />
                <span className="text-sm">Executive Findings ({assetFindings.length})</span>
              </label>
              <label className="flex cursor-pointer items-center gap-3">
                <Checkbox
                  checked={includeActions}
                  onCheckedChange={(c) => setIncludeActions(!!c)}
                />
                <span className="text-sm">Recommended Actions ({assetActions.length})</span>
              </label>
            </div>
          </div>

          {/* Generate Button */}
          <Button variant="action" onClick={handleExport} disabled={isGenerating}>
            {isGenerating ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <FileDown className="mr-2 h-4 w-4" />
                Generate & Download
              </>
            )}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
};
