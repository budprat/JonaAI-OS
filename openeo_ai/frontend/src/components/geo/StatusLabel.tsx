import React from "react";
import { cn } from "@/lib/utils";

interface StatusLabelProps {
  status: "healthy" | "watch" | "at-risk" | "critical";
  className?: string;
}

const statusConfig = {
  healthy: { label: "Healthy", className: "text-status-good" },
  watch: { label: "Watch", className: "text-status-warn" },
  "at-risk": { label: "At Risk", className: "text-status-warn" },
  critical: { label: "Critical", className: "text-status-critical" },
};

export const StatusLabel: React.FC<StatusLabelProps> = ({ status, className }) => {
  const config = statusConfig[status];
  return (
    <span className={cn("font-mono text-xs uppercase font-medium tracking-wider", config.className, className)}>
      {config.label}
    </span>
  );
};
