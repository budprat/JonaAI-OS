// Persona selector for Decision Layer framing
import { User, Briefcase, TrendingUp, Building2 } from "lucide-react";

export type Persona = "analyst" | "farmer" | "insurer" | "investor" | "government";

interface PersonaSelectorProps {
  value: Persona;
  onChange: (persona: Persona) => void;
}

const PERSONAS: Array<{ id: Persona; label: string; icon: typeof User; description: string }> = [
  { id: "analyst", label: "Analyst", icon: User, description: "Technical EO analysis" },
  { id: "farmer", label: "Farmer", icon: User, description: "Crop health & irrigation" },
  { id: "insurer", label: "Insurer", icon: Briefcase, description: "Risk & loss assessment" },
  { id: "investor", label: "Investor", icon: TrendingUp, description: "Yield & ROI analysis" },
  { id: "government", label: "Government", icon: Building2, description: "Policy & compliance" },
];

export function PersonaSelector({ value, onChange }: PersonaSelectorProps) {
  return (
    <div className="flex items-center gap-1 px-1">
      {PERSONAS.map((p) => {
        const Icon = p.icon;
        const isActive = value === p.id;
        return (
          <button
            key={p.id}
            onClick={() => onChange(p.id)}
            title={p.description}
            className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
              isActive
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            <Icon className="w-3 h-3" />
            {p.label}
          </button>
        );
      })}
    </div>
  );
}
