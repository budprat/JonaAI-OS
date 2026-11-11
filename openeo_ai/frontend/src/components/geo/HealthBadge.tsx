import React from "react";
import { cn } from "@/lib/utils";

interface HealthBadgeProps {
  score: number;
  className?: string;
}

export const HealthBadge: React.FC<HealthBadgeProps> = ({ score, className }) => {
  const bg = score >= 70 ? "bg-status-good" : score >= 50 ? "bg-status-warn" : "bg-status-critical";

  return (
    <span
      className={cn(
        "inline-flex items-center justify-center h-6 px-2 font-mono text-xs font-medium text-primary-foreground rounded-md",
        bg,
        className
      )}
    >
      {score}
    </span>
  );
};
