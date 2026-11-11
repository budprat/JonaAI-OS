import React, { useState, useRef, useEffect } from "react";
import { Send, Square, MapPin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BBox } from "@/types";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  onSend: (message: string) => void;
  isProcessing: boolean;
  disabled?: boolean;
  placeholder?: string;
  activeBbox?: BBox | null;
  onStop?: () => void;
}

export function ChatInput({
  onSend,
  isProcessing,
  disabled = false,
  placeholder = "Ask about Earth observation data...",
  activeBbox,
  onStop,
}: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 150)}px`;
    }
  }, [message]);

  const handleSend = () => {
    if (message.trim() && !isProcessing && !disabled) {
      onSend(message.trim());
      setMessage("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-border/40 bg-background/95 backdrop-blur-sm px-4 py-3">
      {activeBbox && (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-1.5 text-xs">
          <MapPin className="h-3.5 w-3.5 text-primary" />
          <span className="font-medium text-primary">BBox:</span>
          <span className="tabular-nums text-muted-foreground">
            {activeBbox.west.toFixed(2)}, {activeBbox.south.toFixed(2)},{" "}
            {activeBbox.east.toFixed(2)}, {activeBbox.north.toFixed(2)}
          </span>
        </div>
      )}

      <div
        className={cn(
          "relative flex items-end rounded-2xl border transition-all duration-200",
          isFocused
            ? "border-primary/40 shadow-[0_0_0_1px_hsl(var(--primary)/0.1)]"
            : "border-border/60 hover:border-border",
        )}
      >
        <textarea
          ref={textareaRef}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          aria-label="Message input - describe your Earth observation analysis"
          className={cn(
            "w-full resize-none bg-transparent px-4 py-3 pr-12 text-base sm:text-sm leading-relaxed",
            "placeholder:text-muted-foreground/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        />

        <div className="absolute bottom-1.5 right-1.5">
          {isProcessing ? (
            <Button
              onClick={onStop}
              size="icon"
              variant="ghost"
              aria-label="Stop generating"
              className="h-10 w-10 sm:h-8 sm:w-8 rounded-xl bg-muted hover:bg-destructive/10 hover:text-destructive transition-colors"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              onClick={handleSend}
              disabled={!message.trim() || disabled}
              size="icon"
              aria-label="Send message"
              className={cn(
                "h-10 w-10 sm:h-8 sm:w-8 rounded-xl transition-all duration-200",
                message.trim() && !disabled
                  ? "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
                  : "bg-muted text-muted-foreground",
              )}
            >
              <Send className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      <p className="mt-1.5 text-center text-[10px] text-muted-foreground/40">
        Jona can make mistakes. Verify important results.
      </p>
    </div>
  );
}
