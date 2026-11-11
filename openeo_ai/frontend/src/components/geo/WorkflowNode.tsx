import React from "react";
import type { WorkflowNode as WFNode } from "@/lib/geo/workflow-templates";
import { Radio, GitBranch, Zap } from "lucide-react";

const typeConfig = {
  trigger: { icon: Radio, borderClass: "border-status-good", bgClass: "bg-status-good/5", labelClass: "text-status-good" },
  condition: { icon: GitBranch, borderClass: "border-status-warn", bgClass: "bg-status-warn/5", labelClass: "text-status-warn" },
  action: { icon: Zap, borderClass: "border-primary", bgClass: "bg-primary/5", labelClass: "text-foreground" },
};

interface Props {
  node: WFNode;
  isSelected: boolean;
  onClick: () => void;
}

export const WorkflowNode: React.FC<Props> = ({ node, isSelected, onClick }) => {
  const config = typeConfig[node.type];
  const Icon = config.icon;

  return (
    <button
      onClick={onClick}
      className={`w-80 border-2 p-4 text-left transition-all ${config.bgClass} ${
        isSelected ? `${config.borderClass} shadow-sm` : "border-border hover:border-muted-foreground/30"
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`w-4 h-4 ${config.labelClass}`} />
        <span className={`font-mono text-[10px] uppercase tracking-wider ${config.labelClass}`}>
          {node.type}
        </span>
      </div>
      <p className="text-sm font-medium">{node.label}</p>
      <p className="text-xs text-muted-foreground mt-1">{node.description}</p>
    </button>
  );
};
