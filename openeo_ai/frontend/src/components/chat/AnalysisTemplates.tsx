import {
  TreePine,
  Flame,
  Droplets,
  Snowflake,
  Building2,
  Thermometer,
  Satellite,
  Mountain,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Template {
  icon: React.ElementType;
  title: string;
  description: string;
  prompt: string;
  color: string;
  category: string;
}

const TEMPLATES: Template[] = [
  {
    icon: TreePine,
    title: "Vegetation Health (NDVI)",
    description:
      "Analyze plant health and biomass density using Sentinel-2 imagery",
    prompt:
      "Calculate NDVI vegetation health index for Kerala, India during June 2024 over a small area of 200x200 pixels",
    color: "text-emerald-500",
    category: "Vegetation",
  },
  {
    icon: Flame,
    title: "Burn Severity (dNBR)",
    description: "Map wildfire damage by comparing pre and post-fire imagery",
    prompt:
      "Analyze burn severity using dNBR for Los Angeles area comparing November 2024 vs January 2025 over a small area of 200x200 pixels",
    color: "text-orange-500",
    category: "Disaster",
  },
  {
    icon: Droplets,
    title: "Flood Extent (MNDWI)",
    description: "Detect flood boundaries using modified water index",
    prompt:
      "Map flood extent using MNDWI water index for Bangladesh during August 2024 monsoon over a small area of 200x200 pixels",
    color: "text-blue-500",
    category: "Disaster",
  },
  {
    icon: Snowflake,
    title: "Snow Cover (NDSI)",
    description: "Monitor snow extent using normalized snow index",
    prompt:
      "Calculate snow cover using NDSI for Swiss Alps during January 2025 over a small area of 200x200 pixels",
    color: "text-cyan-400",
    category: "Climate",
  },
  {
    icon: Building2,
    title: "Urban Growth (NDBI)",
    description: "Track urban expansion using built-up area index",
    prompt:
      "Analyze urban growth using NDBI built-up index for Dubai comparing 2020 vs 2024 over a small area of 200x200 pixels",
    color: "text-purple-500",
    category: "Land Use",
  },
  {
    icon: Thermometer,
    title: "Land Surface Temperature",
    description: "Map surface temperature using Landsat thermal bands",
    prompt:
      "Calculate land surface temperature for New Delhi, India during May 2024 to detect urban heat islands over a small area of 200x200 pixels",
    color: "text-red-500",
    category: "Climate",
  },
  {
    icon: Satellite,
    title: "Cloud-Free Mosaic",
    description: "Find the clearest Sentinel-2 imagery for your area",
    prompt:
      "Find cloud-free Sentinel-2 imagery for Paris, France during summer 2024 and show it on the map over a small area of 200x200 pixels",
    color: "text-sky-500",
    category: "Discovery",
  },
  {
    icon: Mountain,
    title: "Terrain Elevation",
    description: "Visualize elevation data and topographic features",
    prompt:
      "Analyze terrain elevation around Mount Everest and visualize the elevation profile over a small area of 200x200 pixels",
    color: "text-amber-600",
    category: "Terrain",
  },
];

interface AnalysisTemplatesProps {
  onSelectTemplate: (prompt: string) => void;
  className?: string;
}

export function AnalysisTemplates({
  onSelectTemplate,
  className,
}: AnalysisTemplatesProps) {
  return (
    <div className={cn("grid grid-cols-2 gap-2 px-1", className)}>
      {TEMPLATES.map((template, index) => (
        <button
          key={index}
          onClick={() => onSelectTemplate(template.prompt)}
          className="stagger-enter group flex flex-col gap-1.5 rounded-xl border border-border/60 bg-card p-3 text-left transition-all duration-200 hover:border-primary/30 hover:bg-primary/[0.02] hover:shadow-sm active:scale-[0.98]"
          style={{ animationDelay: `${index * 60}ms` }}
        >
          <div className="flex items-center gap-2">
            <template.icon className={cn("h-4 w-4 shrink-0", template.color)} />
            <span className="text-xs font-medium text-foreground line-clamp-1">
              {template.title}
            </span>
          </div>
          <span className="text-[10px] leading-tight text-muted-foreground/70 line-clamp-2">
            {template.description}
          </span>
          <span className="mt-auto inline-flex self-start rounded-full bg-muted px-1.5 py-0.5 text-[9px] font-medium text-muted-foreground">
            {template.category}
          </span>
        </button>
      ))}
    </div>
  );
}
