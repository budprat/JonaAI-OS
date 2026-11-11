import React, { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface SidebarProps {
  position: "left" | "right";
  defaultCollapsed?: boolean;
  tabs: {
    id: string;
    label: string;
    icon: React.ReactNode;
    content: React.ReactNode;
  }[];
  className?: string;
  isMobile?: boolean;
}

export function Sidebar({
  position,
  defaultCollapsed = false,
  tabs,
  className,
  isMobile = false,
}: SidebarProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [activeTab, setActiveTab] = useState(tabs[0]?.id);

  // Mobile: render full-width tabbed content without sidebar wrapper
  if (isMobile) {
    return (
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex h-full w-full flex-col overflow-hidden"
      >
        {tabs.length > 1 && (
          <TabsList className="mx-3 mt-3 shrink-0 flex overflow-x-auto rounded-lg bg-muted/50 p-1 gap-1">
            {tabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="flex shrink-0 items-center justify-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all data-[state=active]:bg-background data-[state=active]:shadow-sm"
              >
                {tab.icon}
                <span className="truncate">{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        )}
        <div className="flex-1 overflow-hidden" style={{ minHeight: 0 }}>
          {tabs.map((tab) => (
            <TabsContent
              key={tab.id}
              value={tab.id}
              className="m-0 h-full overflow-y-auto p-0"
            >
              {tab.content}
            </TabsContent>
          ))}
        </div>
      </Tabs>
    );
  }

  return (
    <div
      className={cn(
        "relative flex h-full flex-col overflow-hidden bg-sidebar transition-all duration-300 ease-out",
        position === "left"
          ? "border-r border-sidebar-border"
          : "border-l border-sidebar-border",
        collapsed ? "w-12" : position === "left" ? "w-96" : "w-80",
        className,
      )}
    >
      {/* Collapse Toggle */}
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setCollapsed(!collapsed)}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
        className={cn(
          "absolute top-3 z-10 h-7 w-7 rounded-lg border border-border/50 bg-background shadow-sm transition-all duration-200 hover:shadow-md",
          position === "left"
            ? collapsed
              ? "right-2.5"
              : "-right-3.5"
            : collapsed
              ? "left-2.5"
              : "-left-3.5",
        )}
      >
        {position === "left" ? (
          collapsed ? (
            <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <ChevronLeft className="h-3.5 w-3.5" />
          )
        ) : collapsed ? (
          <ChevronLeft className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
      </Button>

      {collapsed ? (
        // Collapsed — icon buttons with tooltips
        <div className="flex flex-col items-center gap-1.5 pt-14">
          {tabs.map((tab) => (
            <Button
              key={tab.id}
              variant={activeTab === tab.id ? "secondary" : "ghost"}
              size="icon"
              onClick={() => {
                setActiveTab(tab.id);
                setCollapsed(false);
              }}
              className={cn(
                "h-8 w-8 rounded-lg transition-all duration-200",
                activeTab === tab.id && "shadow-sm",
              )}
              title={tab.label}
              aria-label={tab.label}
            >
              {tab.icon}
            </Button>
          ))}
        </div>
      ) : (
        // Expanded — tabs with content
        <Tabs
          value={activeTab}
          onValueChange={setActiveTab}
          className="flex h-full flex-col overflow-hidden"
        >
          {tabs.length > 1 && (
            <TabsList
              className="mx-3 mt-12 shrink-0 grid rounded-lg bg-muted/50 p-1"
              style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }}
            >
              {tabs.map((tab) => (
                <Tooltip key={tab.id}>
                  <TooltipTrigger asChild>
                    <TabsTrigger
                      value={tab.id}
                      className="flex items-center justify-center gap-1 rounded-md px-1 text-xs font-medium transition-all data-[state=active]:bg-background data-[state=active]:shadow-sm"
                    >
                      {tab.icon}
                      {tabs.length <= 3 && (
                        <span className="truncate">{tab.label}</span>
                      )}
                    </TabsTrigger>
                  </TooltipTrigger>
                  {tabs.length > 3 && (
                    <TooltipContent side="bottom" className="text-xs">
                      {tab.label}
                    </TooltipContent>
                  )}
                </Tooltip>
              ))}
            </TabsList>
          )}

          <div
            className={cn(
              "flex-1 overflow-hidden",
              tabs.length === 1 && "pt-10",
            )}
            style={{ minHeight: 0 }}
          >
            {tabs.map((tab) => (
              <TabsContent
                key={tab.id}
                value={tab.id}
                className="m-0 h-full overflow-y-auto p-0"
              >
                {tab.content}
              </TabsContent>
            ))}
          </div>
        </Tabs>
      )}
    </div>
  );
}
