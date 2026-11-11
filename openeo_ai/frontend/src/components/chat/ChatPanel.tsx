import { useRef, useEffect, useState, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { WifiOff, RefreshCw, Sparkles, ArrowDown } from "lucide-react";
import {
  Message,
  ThinkingStep,
  BBox,
  ClarificationQuestion,
  UIMessage,
} from "@/types";
import { ChatMessage } from "./ChatMessage";
import { ChatInput } from "./ChatInput";
import { ClarificationCard } from "./ClarificationCard";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { SuggestionChips } from "./SuggestionChips";
import { AnalysisTemplates } from "./AnalysisTemplates";
import { Button } from "@/components/ui/button";
import type { ConnectionState } from "@/hooks/useWebSocket";

interface ChatPanelProps {
  messages: Message[];
  /** AI SDK v5-style messages with typed parts array. Used for rendering when provided. */
  uiMessages?: UIMessage[];
  onSendMessage: (content: string) => void;
  isProcessing: boolean;
  isConnected: boolean;
  /** Finer than isConnected: says whether a reconnect can help or the user must sign in. */
  connectionState?: ConnectionState;
  /** Leaves the app for the login page, when the server refused the sign-in. */
  onSignIn?: () => void;
  thinkingSteps?: ThinkingStep[];
  onReconnect?: () => void;
  activeBbox?: BBox | null;
  onStop?: () => void;
  contextSuggestions?: string[];
  pendingClarification?: ClarificationQuestion[] | null;
  onClarificationSubmit?: (answers: Record<string, string | string[]>) => void;
}

// SUGGESTION_CARDS replaced by AnalysisTemplates component

interface ConnectionBanner {
  text: string;
  action: "reconnect" | "reload" | "sign_in";
}

/** What to tell the user about the connection, or null when it is working. */
export function connectionBanner(
  state: ConnectionState,
  isProcessing: boolean,
): ConnectionBanner | null {
  switch (state) {
    case "open":
    case "authenticating":
      return null;
    case "access_expired":
      return {
        text: "Your Cloudflare sign-in expired. Sign in again; any analysis keeps running meanwhile.",
        action: "reload",
      };
    case "signed_out":
      return { text: "Your sign-in was refused or has expired.", action: "sign_in" };
    case "connecting":
      return { text: "Connecting…", action: "reconnect" };
    case "reconnecting":
      return {
        text: isProcessing ? "Reconnecting. Your analysis keeps running." : "Reconnecting…",
        action: "reconnect",
      };
  }
}

/** Threshold (items from end) below which we consider the user "at the bottom". */
const NEAR_BOTTOM_THRESHOLD = 80; // px
const SCROLL_BTN_THRESHOLD = 200; // px

export function ChatPanel({
  messages,
  uiMessages,
  onSendMessage,
  isProcessing,
  isConnected,
  connectionState,
  onSignIn,
  thinkingSteps = [],
  onReconnect,
  activeBbox,
  onStop,
  contextSuggestions = [],
  pendingClarification,
  onClarificationSubmit,
}: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const isNearBottomRef = useRef(true);

  // The items rendered in the virtual list
  const renderItems = uiMessages ?? messages;

  const virtualizer = useVirtualizer({
    count: renderItems.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 120, // rough initial estimate per message
    overscan: 8, // render extra items above/below for smooth scrolling
  });

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  // Auto-scroll when new messages arrive (only if near bottom)
  useEffect(() => {
    if (isNearBottomRef.current) {
      // Use rAF to ensure DOM has updated with new virtualizer sizes
      requestAnimationFrame(() => scrollToBottom());
    }
  }, [renderItems.length, thinkingSteps, scrollToBottom]);

  // Track scroll position
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    isNearBottomRef.current = distFromBottom < NEAR_BOTTOM_THRESHOLD;
    setShowScrollBtn(distFromBottom > SCROLL_BTN_THRESHOLD);
  }, []);

  const hasMessages = renderItems.length > 0;
  const banner = connectionBanner(
    connectionState ?? (isConnected ? "open" : "reconnecting"),
    isProcessing,
  );

  return (
    <div className="flex h-full flex-col">
      {/* Connection banner: what is wrong, and the one action that fixes it */}
      {banner && (
        <div
          className="flex items-center justify-between gap-2 border-b border-destructive/20 bg-destructive/5 px-4 py-2"
          data-testid="connection-banner"
        >
          <div className="flex items-center gap-2 text-sm text-destructive">
            <WifiOff className="h-4 w-4" />
            <span className="font-medium">{banner.text}</span>
          </div>
          {banner.action === "reload" && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => window.location.reload()}
              className="h-7 shrink-0 gap-1.5 border-destructive/20 text-destructive hover:bg-destructive/5"
            >
              <RefreshCw className="h-3 w-3" />
              Sign in again
            </Button>
          )}
          {banner.action === "sign_in" && onSignIn && (
            <Button
              variant="outline"
              size="sm"
              onClick={onSignIn}
              className="h-7 shrink-0 gap-1.5 border-destructive/20 text-destructive hover:bg-destructive/5"
            >
              Sign in
            </Button>
          )}
          {banner.action === "reconnect" && onReconnect && (
            <Button
              variant="outline"
              size="sm"
              onClick={onReconnect}
              className="h-7 shrink-0 gap-1.5 border-destructive/20 text-destructive hover:bg-destructive/5"
            >
              <RefreshCw className="h-3 w-3" />
              Reconnect
            </Button>
          )}
        </div>
      )}

      {/* Messages area */}
      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          role="log"
          aria-label="Chat conversation"
          aria-live="polite"
          className="h-full overflow-y-auto"
        >
          {!hasMessages ? (
            <div className="flex h-full flex-col items-center justify-center px-6 py-12">
              {/* Logo */}
              <div className="animate-float mb-6">
                <img
                  src="/jonaai.png"
                  alt="Jona AI"
                  className="h-16 w-auto drop-shadow-md dark:invert"
                />
              </div>

              <h2 className="mb-2 text-xl font-semibold tracking-tight">
                Jona AI Assistant
              </h2>
              <p className="mb-6 max-w-[300px] text-center text-sm leading-relaxed text-muted-foreground">
                Analyze Earth observation data using natural language.
              </p>

              {/* Analysis templates gallery */}
              <AnalysisTemplates
                onSelectTemplate={onSendMessage}
                className="w-full max-w-[420px]"
              />
            </div>
          ) : (
            <div className="py-2">
              {/* Virtualized message list */}
              <div
                style={{
                  height: virtualizer.getTotalSize(),
                  width: "100%",
                  position: "relative",
                }}
              >
                {virtualizer.getVirtualItems().map((virtualItem) => {
                  const item = renderItems[virtualItem.index];
                  return (
                    <div
                      key={virtualItem.key}
                      data-index={virtualItem.index}
                      ref={virtualizer.measureElement}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${virtualItem.start}px)`,
                      }}
                    >
                      {uiMessages ? (
                        <ChatMessage message={item as UIMessage} />
                      ) : (
                        <ChatMessage message={item as never} />
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Clarification card */}
              {pendingClarification && onClarificationSubmit && (
                <ClarificationCard
                  questions={pendingClarification}
                  onSubmit={onClarificationSubmit}
                />
              )}

              {/* Suggestion chips after last assistant message */}
              {!isProcessing && contextSuggestions.length > 0 && (
                <div className="px-4 py-2">
                  <SuggestionChips
                    suggestions={contextSuggestions}
                    onSelect={onSendMessage}
                    className="flex-wrap"
                  />
                </div>
              )}

              {/* Thinking indicator */}
              {(isProcessing || thinkingSteps.length > 0) && (
                <div className="flex gap-3 px-4 py-2">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary/20 to-primary/5">
                    <Sparkles className="h-3.5 w-3.5 text-primary" />
                  </div>
                  <div className="flex-1 pt-0.5">
                    <ThinkingIndicator
                      steps={thinkingSteps}
                      isProcessing={isProcessing}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Scroll to bottom button */}
        {showScrollBtn && (
          <button
            onClick={scrollToBottom}
            className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full border border-border bg-background/95 px-3 py-1.5 text-xs font-medium text-muted-foreground shadow-lg backdrop-blur-sm transition-all hover:bg-muted hover:text-foreground"
          >
            <ArrowDown className="h-3 w-3" />
            Scroll to bottom
          </button>
        )}
      </div>

      <ChatInput
        onSend={onSendMessage}
        isProcessing={isProcessing}
        placeholder={
          !isConnected
            ? "Offline. Your message will send when reconnected."
            : "Ask about Earth observation data..."
        }
        activeBbox={activeBbox}
        onStop={onStop}
      />
    </div>
  );
}
