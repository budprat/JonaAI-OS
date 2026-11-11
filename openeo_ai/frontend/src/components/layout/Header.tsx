import {
  Settings,
  HelpCircle,
  Moon,
  Sun,
  Wifi,
  WifiOff,
  Plus,
  LogOut,
  LayoutDashboard,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface HeaderProps {
  isConnected: boolean;
  isDark?: boolean;
  onToggleTheme?: () => void;
  onHelp?: () => void;
  onSettings?: () => void;
  onNewChat?: () => void;
  username?: string | null;
  onLogout?: () => void;
}

export function Header({
  isConnected,
  isDark = false,
  onToggleTheme,
  onHelp,
  onSettings,
  onNewChat,
  username,
  onLogout,
}: HeaderProps) {
  const navigate = useNavigate();
  return (
    <header className="glass sticky top-0 z-50 flex h-14 sm:h-16 items-center justify-between px-3 sm:px-5 pt-safe">
      <div className="flex items-center gap-2 sm:gap-3">
        <div className="group flex items-center gap-2 sm:gap-2.5 cursor-pointer select-none">
          <div className="relative animate-logo-enter">
            <img
              src="/jonaai.png"
              alt="Jona AI"
              className="h-8 sm:h-10 w-auto drop-shadow-sm transition-all duration-300 group-hover:scale-110 group-hover:drop-shadow-md dark:invert"
            />
            <div className="absolute inset-0 rounded-full bg-primary/0 transition-all duration-300 group-hover:bg-primary/10 group-hover:blur-xl" />
          </div>
          <span className="hidden sm:inline animate-logo-enter text-lg font-semibold tracking-tight opacity-0 [animation-delay:150ms]">
            Jona AI
          </span>
        </div>
        <Badge
          variant="outline"
          className="hidden sm:inline-flex animate-logo-enter text-[10px] font-medium uppercase tracking-widest opacity-0 [animation-delay:300ms]"
        >
          v0.2.0
        </Badge>
      </div>

      <div className="flex items-center gap-1 sm:gap-1.5">
        <Badge
          variant={isConnected ? "success" : "destructive"}
          className={cn(
            "flex items-center gap-1 sm:gap-1.5 transition-all duration-300",
            isConnected ? "bg-success pulse-ring" : "bg-destructive",
          )}
        >
          {isConnected ? (
            <>
              <Wifi className="h-3 w-3" />
              <span className="hidden sm:inline text-[10px] font-medium uppercase tracking-wider">
                Connected
              </span>
            </>
          ) : (
            <>
              <WifiOff className="h-3 w-3" />
              <span className="hidden sm:inline text-[10px] font-medium uppercase tracking-wider">
                Offline
              </span>
            </>
          )}
        </Badge>

        <div className="ml-0.5 sm:ml-1 flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/dashboard")}
            aria-label="Open Geo Hub preview"
            className="h-9 w-9 sm:w-auto gap-1.5 rounded-lg px-2 sm:px-3 text-muted-foreground transition-colors hover:text-foreground"
          >
            <LayoutDashboard className="h-4 w-4" />
            <span className="hidden sm:inline text-xs font-medium">
              Geo Hub
            </span>
          </Button>

          <Button
            variant="ghost"
            size="sm"
            onClick={onNewChat}
            aria-label="Start new chat"
            className="h-9 w-9 sm:w-auto gap-1.5 rounded-lg px-2 sm:px-3 text-muted-foreground transition-colors hover:text-foreground"
          >
            <Plus className="h-4 w-4" />
            <span className="hidden sm:inline text-xs font-medium">
              New Chat
            </span>
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleTheme}
            aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
            className="h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
          >
            {isDark ? (
              <Sun className="h-4 w-4" />
            ) : (
              <Moon className="h-4 w-4" />
            )}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={onHelp}
            aria-label="Show help"
            className="hidden sm:inline-flex h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
          >
            <HelpCircle className="h-4 w-4" />
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={onSettings}
            aria-label="Open settings"
            className="h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
          >
            <Settings className="h-4 w-4" />
          </Button>

          {username && (
            <>
              <span
                className="hidden sm:inline ml-1 text-xs text-muted-foreground"
                data-testid="text-username"
              >
                {username}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={onLogout}
                aria-label="Log out"
                className="h-9 w-9 rounded-lg text-muted-foreground transition-colors hover:text-foreground"
                data-testid="button-logout"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
