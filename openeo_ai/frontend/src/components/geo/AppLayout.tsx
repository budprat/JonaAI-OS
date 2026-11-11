import React, { useState, useCallback } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3,
  Activity,
  GitBranch,
  Calculator,
  FlaskConical,
  Users,
  Store,
  Wrench,
  Radio,
  Orbit,
  Bell,
  Zap,
  Moon,
  Sun,
  MessageSquare,
  LogOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { AlertSubscriptions, getAlertCount } from "@/components/geo/AlertSubscriptions";

interface NavItem {
  path: string;
  label: string;
  icon: React.ElementType;
  group: "operations" | "tools";
  exact?: boolean;
}

const navItems: NavItem[] = [
  {
    path: "/dashboard",
    label: "Portfolio",
    icon: BarChart3,
    group: "operations",
    exact: true,
  },
  {
    path: "/dashboard/monitoring",
    label: "Monitoring",
    icon: Radio,
    group: "operations",
  },
  {
    path: "/dashboard/satellites",
    label: "Satellites",
    icon: Orbit,
    group: "operations",
  },
  {
    path: "/dashboard/interventions",
    label: "Interventions",
    icon: Wrench,
    group: "operations",
  },
  { path: "/dashboard/workflows", label: "Workflows", icon: GitBranch, group: "tools" },
  { path: "/dashboard/roi", label: "ROI", icon: Calculator, group: "tools" },
  {
    path: "/dashboard/scenarios",
    label: "Scenarios",
    icon: FlaskConical,
    group: "tools",
  },
  { path: "/dashboard/marketplace", label: "Marketplace", icon: Store, group: "tools" },
  { path: "/dashboard/community", label: "Community", icon: Users, group: "tools" },
];

interface GeoAppLayoutProps {
  username?: string | null;
  onLogout?: () => void;
}

const GeoAppLayout: React.FC<GeoAppLayoutProps> = ({ username, onLogout }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains("dark"));
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [alertCount, setAlertCount] = useState(getAlertCount);
  const liveRoutePrefixes = [
    "/dashboard/analysis/",
    "/dashboard/asset/",
    "/dashboard/evidence/",
    "/dashboard/monitoring",
    "/dashboard/satellites",
    "/dashboard/workflows",
    "/dashboard/marketplace",
  ];
  const isLiveMetadataRoute =
    location.pathname === "/dashboard" ||
    liveRoutePrefixes.some((prefix) => location.pathname.startsWith(prefix));
  const analysisRoute = location.pathname.match(/^\/dashboard\/analysis\/([^/]+)(?:\/evidence)?$/);
  const legacyAssetRoute = location.pathname.match(/^\/dashboard\/asset\/([^/]+)(?:\/evidence)?$/);
  const legacyEvidenceRoute = location.pathname.match(/^\/dashboard\/evidence\/([^/]+)$/);
  const detailAnalysisId =
    analysisRoute?.[1] ?? legacyAssetRoute?.[1] ?? legacyEvidenceRoute?.[1] ?? null;
  const isEvidenceDetailRoute =
    location.pathname.includes("/evidence") || location.pathname.startsWith("/dashboard/evidence/");

  const toggleTheme = useCallback(() => {
    document.documentElement.classList.toggle("dark");
    setIsDark((prev) => !prev);
  }, []);

  const isActive = (item: NavItem) =>
    item.exact ? location.pathname === item.path : location.pathname.startsWith(item.path);

  const operations = navItems.filter((n) => n.group === "operations");
  const tools = navItems.filter((n) => n.group === "tools");

  const handleAlertCountChange = useCallback((count: number) => {
    setAlertCount(count);
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* Top Navigation */}
      <header className="glass sticky top-0 z-50 flex h-14 sm:h-16 flex-shrink-0 items-center px-6 pt-safe">
        <div className="mr-4 flex items-center gap-2">
          <img
            src="/jonaai.png"
            alt="Jona AI"
            className="h-8 sm:h-10 w-auto drop-shadow-sm transition-all duration-300 hover:scale-110 hover:drop-shadow-md dark:invert"
          />
          <span className="text-sm font-semibold tracking-tight">JonaAI</span>
        </div>

        {/* AI Workspace link */}
        <button
          onClick={() => navigate("/app")}
          className={`mr-1 flex items-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
            location.pathname.startsWith("/app")
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-accent"
          }`}
        >
          <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
          AI Chat
        </button>

        <div className="mx-1.5 h-4 w-px bg-border" />

        {/* Operations group  full labels */}
        <nav className="flex items-center gap-1">
          {operations.map((item) => {
            const Icon = item.icon;
            const active = isActive(item);
            return (
              <button
                key={item.path}
                onClick={() => navigate(item.path)}
                className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent"
                }`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="mx-1.5 h-4 w-px bg-border" />

        {/* Tools group  icon-only with tooltips */}
        <nav className="flex items-center gap-1">
          {tools.map((item) => {
            const Icon = item.icon;
            const active = isActive(item);
            return (
              <button
                key={item.path}
                onClick={() => navigate(item.path)}
                title={item.label}
                aria-label={item.label}
                className={`rounded-md p-1.5 transition-colors ${
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent"
                }`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            );
          })}
        </nav>

        {/* Breadcrumb for detail pages  clickable */}
        {detailAnalysisId && (
          <div className="ml-2 flex items-center gap-0.5">
            <button
              onClick={() => navigate(`/dashboard/analysis/${detailAnalysisId}`)}
              className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
                !isEvidenceDetailRoute
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              <Activity className="h-3.5 w-3.5" aria-hidden="true" />
              Briefing
            </button>
            <button
              onClick={() => navigate(`/dashboard/analysis/${detailAnalysisId}/evidence`)}
              className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
                isEvidenceDetailRoute
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              Evidence
            </button>
          </div>
        )}

        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-2 rounded-md border border-border bg-card/70 px-2.5 py-1 font-mono text-[10px] text-muted-foreground">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                isLiveMetadataRoute ? "bg-sky-500" : "bg-amber-500"
              }`}
            />
            <span className="uppercase tracking-wider">
              {isLiveMetadataRoute ? "LIVE METADATA" : "PREVIEW"}
            </span>
            <span className="text-border">|</span>
            <span>
              {isLiveMetadataRoute
                ? "Projects + saved analyses"
                : "Sample data on this page"}
            </span>
          </div>

          {/* Workflow Run shortcut */}
          <button
            onClick={() => navigate("/dashboard/workflows")}
            className="flex items-center gap-1.5 border border-border px-2.5 py-1 font-mono text-[10px] text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
            title="Workflow Builder"
            aria-label="Run Workflow"
          >
            <Zap className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="hidden uppercase tracking-wider sm:inline">Run</span>
          </button>

          {/* Theme Toggle */}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label="Toggle dark mode"
            className="h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
          >
            {isDark ? (
              <Sun className="h-4 w-4" aria-hidden="true" />
            ) : (
              <Moon className="h-4 w-4" aria-hidden="true" />
            )}
          </Button>

          {/* Alert Bell */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setAlertsOpen(true)}
            aria-label="Alert Subscriptions"
            className="relative h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
          >
            <Bell className="h-4 w-4" aria-hidden="true" />
            {alertCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-red-500 font-mono text-[9px] font-medium text-white">
                {alertCount}
              </span>
            )}
          </Button>

          {/* User + Logout */}
          {username && <span className="font-mono text-xs text-muted-foreground">{username}</span>}
          {onLogout && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onLogout}
              aria-label="Logout"
              title="Logout"
              className="h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
            >
              <LogOut className="h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </header>

      <div className="border-b border-border bg-muted/40 px-6 py-2 text-xs text-muted-foreground">
        {isLiveMetadataRoute
          ? "This Geo Hub route is backed by persisted JonaAI platform data."
          : "Preview route: this Geo Hub page currently uses sample data and does not reflect your live project state yet."}
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>

      {/* Alert Subscriptions Modal */}
      <AlertSubscriptions
        open={alertsOpen}
        onOpenChange={setAlertsOpen}
        onCountChange={handleAlertCountChange}
      />
    </div>
  );
};

export default GeoAppLayout;
