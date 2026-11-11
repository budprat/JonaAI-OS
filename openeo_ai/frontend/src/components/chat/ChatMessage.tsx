import { useState, useCallback, memo } from "react";
import {
  User,
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  Wrench,
  AlertCircle,
  ThumbsUp,
  ThumbsDown,
  AlertTriangle,
  Link2,
} from "lucide-react";
import { scoreTrace } from "@/lib/langfuse";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";

SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("py", python);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("js", javascript);
SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("ts", typescript);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("sh", bash);
SyntaxHighlighter.registerLanguage("shell", bash);
SyntaxHighlighter.registerLanguage("sql", sql);
import type { UIMessage, ToolCallState } from "@/types";
import { isToolCallPart, isToolResultPart, getTextContent } from "@/types";
import { ToolResultCard } from "./ToolResultCard";
import { cn } from "@/lib/utils";

// Module-level markdown components (avoids re-creation on each render)
const markdownComponents: Partial<Components> = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const codeString = String(children).replace(/\n$/, "");

    if (match) {
      return <CodeBlock language={match[1]!} code={codeString} />;
    }

    return (
      <code
        className="rounded bg-muted px-1.5 py-0.5 font-mono text-[13px] text-foreground"
        {...props}
      >
        {children}
      </code>
    );
  },
  p({ children }: { children?: React.ReactNode }) {
    return <p className="mb-2 last:mb-0">{children}</p>;
  },
  ul({ children }: { children?: React.ReactNode }) {
    return <ul className="mb-2 ml-4 list-disc space-y-1 last:mb-0">{children}</ul>;
  },
  ol({ children }: { children?: React.ReactNode }) {
    return <ol className="mb-2 ml-4 list-decimal space-y-1 last:mb-0">{children}</ol>;
  },
  li({ children }: { children?: React.ReactNode }) {
    return <li className="text-sm">{children}</li>;
  },
  h1({ children }: { children?: React.ReactNode }) {
    return <h1 className="mb-2 mt-4 text-lg font-semibold first:mt-0">{children}</h1>;
  },
  h2({ children }: { children?: React.ReactNode }) {
    return <h2 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h2>;
  },
  h3({ children }: { children?: React.ReactNode }) {
    return <h3 className="mb-1.5 mt-2 text-sm font-semibold first:mt-0">{children}</h3>;
  },
  a({ href, children }: { href?: string; children?: React.ReactNode }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline hover:text-primary/80"
      >
        {children}
      </a>
    );
  },
  blockquote({ children }: { children?: React.ReactNode }) {
    return (
      <blockquote className="mb-2 border-l-2 border-primary/30 pl-3 italic text-muted-foreground">
        {children}
      </blockquote>
    );
  },
  table({ children }: { children?: React.ReactNode }) {
    return (
      <div className="mb-2 overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">{children}</table>
      </div>
    );
  },
  thead({ children }: { children?: React.ReactNode }) {
    return <thead className="bg-muted/50">{children}</thead>;
  },
  th({ children }: { children?: React.ReactNode }) {
    return <th className="px-3 py-1.5 text-left font-medium">{children}</th>;
  },
  td({ children }: { children?: React.ReactNode }) {
    return <td className="border-t border-border px-3 py-1.5">{children}</td>;
  },
  hr() {
    return <hr className="my-3 border-border" />;
  },
  strong({ children }: { children?: React.ReactNode }) {
    return <strong className="font-semibold text-foreground">{children}</strong>;
  },
};

/** Map ToolCallPart.state to ToolCallCard status string. */
function toolCallStateToStatus(
  state: ToolCallState
): "pending" | "running" | "completed" | "error" {
  switch (state) {
    case "running":
      return "running";
    case "result":
      return "completed";
    case "error":
      return "error";
    default:
      return "pending";
  }
}

interface ChatMessageProps {
  message: UIMessage;
}

export const ChatMessage = memo(function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const [copied, setCopied] = useState(false);
  const [hovering, setHovering] = useState(false);
  // Phase D: track thumbs feedback state (1 = up, -1 = down, 0 = none)
  const [feedback, setFeedback] = useState<0 | 1 | -1>(0);
  const isStreaming = message.metadata?.isStreaming === true;
  const traceId = message.metadata?.traceId;
  const groundingWarnings = message.metadata?.groundingWarnings ?? [];
  const sources = message.metadata?.sources ?? [];

  const textContent = getTextContent(message);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(textContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    // Phase E: implicit-copy signal — positive signal that the output was useful
    scoreTrace(traceId, "implicit-copy", 1, "NUMERIC", "user copied response");
  }, [textContent, traceId]);

  const handleThumbs = useCallback(
    (rating: 1 | -1) => {
      setFeedback(rating);
      scoreTrace(
        traceId,
        "user-thumbs",
        rating === 1 ? 1 : 0,
        "BOOLEAN",
        `thumbs-${rating === 1 ? "up" : "down"}`
      );
    },
    [traceId]
  );

  if (isSystem) {
    return (
      <div className="message-enter px-4 py-2">
        <div className="flex items-start gap-2 rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-3 py-2 text-sm text-yellow-800 dark:text-yellow-200">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-500" />
          <span>{textContent}</span>
        </div>
      </div>
    );
  }

  if (message.role === "tool") {
    const toolResultParts = message.parts.filter(isToolResultPart);
    return (
      <div className="message-enter px-4 py-1.5">
        <div className="ml-10">
          {toolResultParts.map((part) => (
            <ToolResultCard key={part.toolCallId} toolName={part.toolName} result={part.output} />
          ))}
        </div>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="message-enter px-4 py-3">
        <div className="flex items-start gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <User className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1 pt-0.5">
            <p className="mb-1 text-sm font-medium text-foreground/80">You</p>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
              {textContent}
            </p>
          </div>
        </div>
      </div>
    );
  }

  // Assistant message — render from parts
  const toolCallParts = message.parts.filter(isToolCallPart);

  return (
    <div
      className="message-enter group relative px-4 py-3"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-gradient-to-br from-primary/20 to-primary/5">
          <img src="/jonaai.png" alt="Jona AI" className="h-5 w-5 object-contain dark:invert" />
        </div>
        <div className="min-w-0 flex-1 pt-0.5">
          <p className="mb-1.5 text-sm font-medium text-primary/80">Jona</p>

          {/* Render text parts */}
          {textContent && (
            <div className="prose-chat text-sm leading-relaxed">
              {isStreaming ? (
                <span className="whitespace-pre-wrap">
                  {textContent}
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-primary/70 align-text-bottom" />
                </span>
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={markdownComponents}
                  skipHtml={true}
                  disallowedElements={["script", "iframe", "object", "embed", "form"]}
                >
                  {textContent}
                </ReactMarkdown>
              )}
            </div>
          )}

          {/* Render tool call parts */}
          {toolCallParts.length > 0 && (
            <div className="mt-2 flex flex-col gap-1.5">
              {toolCallParts.map((part) => (
                <ToolCallCard
                  key={part.toolCallId}
                  toolCall={{
                    id: part.toolCallId,
                    name: part.toolName,
                    arguments: part.input,
                    status: toolCallStateToStatus(part.state),
                  }}
                />
              ))}
            </div>
          )}

          {/* Numbers the grounding check couldn't trace to a tool result: flagged, not removed */}
          {!isUser && groundingWarnings.length > 0 && (
            <div
              className="mt-2 flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-700 dark:text-amber-300"
              role="note"
              data-testid="grounding-warning"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                Not backed by a tool result in this answer:{" "}
                {groundingWarnings.map((v) => String(v.token || v.value)).join(", ")}. Check
                these before relying on them.
              </span>
            </div>
          )}

          {!isUser && sources.length > 0 && (
            <div className="mt-2 text-xs text-muted-foreground" data-testid="message-sources">
              <div className="mb-0.5 flex items-center gap-1 font-medium">
                <Link2 className="h-3 w-3" />
                Sources
              </div>
              <ul className="ml-4 list-disc space-y-0.5">
                {sources.map((source) => (
                  <li key={source} className="break-all">
                    {/^https?:\/\//.test(source) ? (
                      <a href={source} target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground">
                        {source}
                      </a>
                    ) : (
                      source
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Action buttons (copy + thumbs) - appears on hover */}
        <div
          className={cn(
            "flex shrink-0 flex-col gap-1 pt-6 transition-opacity duration-150",
            hovering || feedback !== 0 ? "opacity-100" : "opacity-0"
          )}
        >
          <button
            onClick={handleCopy}
            className="rounded-md p-1.5 text-muted-foreground/60 transition-colors hover:bg-muted hover:text-muted-foreground"
            title="Copy message"
            aria-label={copied ? "Message copied" : "Copy message"}
            data-testid="button-copy-message"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-success" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>

          {/* Phase D: Thumbs feedback — only on assistant messages with a traceId */}
          {!isUser && traceId && (
            <>
              <button
                onClick={() => handleThumbs(1)}
                disabled={feedback !== 0}
                className={cn(
                  "rounded-md p-1.5 transition-colors hover:bg-muted",
                  feedback === 1
                    ? "text-success"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                  feedback !== 0 && feedback !== 1 && "opacity-40"
                )}
                title="Good response"
                aria-label="Thumbs up — good response"
                data-testid="button-thumbs-up"
              >
                <ThumbsUp className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => handleThumbs(-1)}
                disabled={feedback !== 0}
                className={cn(
                  "rounded-md p-1.5 transition-colors hover:bg-muted",
                  feedback === -1
                    ? "text-destructive"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                  feedback !== 0 && feedback !== -1 && "opacity-40"
                )}
                title="Bad response"
                aria-label="Thumbs down — bad response"
                data-testid="button-thumbs-down"
              >
                <ThumbsDown className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
});

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group/code relative my-2 overflow-hidden rounded-lg border border-border bg-[#282c34]">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5">
        <span className="text-[11px] font-medium text-white/50">{language}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-2 py-0.5 text-[11px] text-white/50 transition-colors hover:bg-white/10 hover:text-white/80"
          aria-label={copied ? "Code copied to clipboard" : `Copy ${language} code`}
        >
          {copied ? (
            <>
              <Check className="h-3 w-3" />
              <span>Copied</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <SyntaxHighlighter
        style={oneDark}
        language={language}
        PreTag="div"
        customStyle={{
          margin: 0,
          padding: "12px 16px",
          background: "transparent",
          fontSize: "13px",
          lineHeight: "1.5",
        }}
        codeTagProps={{
          style: {
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          },
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

interface ToolCallCardData {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "pending" | "running" | "completed" | "error";
}

function ToolCallCard({ toolCall }: { toolCall: ToolCallCardData }) {
  const [expanded, setExpanded] = useState(false);

  const friendlyName = toolCall.name
    .replace(/^openeo_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const statusConfig = {
    pending: {
      color: "text-muted-foreground",
      bg: "bg-muted/50",
      label: "Pending",
    },
    running: {
      color: "text-primary",
      bg: "bg-primary/5 border border-primary/20",
      label: "Running",
    },
    completed: {
      color: "text-success",
      bg: "bg-success/5 border border-success/20",
      label: "Done",
    },
    error: {
      color: "text-destructive",
      bg: "bg-destructive/5 border border-destructive/20",
      label: "Error",
    },
  };

  const status = statusConfig[toolCall.status];

  return (
    <div className={cn("rounded-lg px-3 py-2 text-xs transition-all", status.bg)}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2"
        aria-expanded={expanded}
        aria-label={
          expanded ? `Collapse ${friendlyName} details` : `Expand ${friendlyName} details`
        }
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted-foreground" />
        )}
        <Wrench className={cn("h-3 w-3", status.color)} />
        <span className="font-medium text-foreground">{friendlyName}</span>
        {toolCall.status === "running" && (
          <span className="ml-1 inline-flex gap-0.5">
            <span className="h-1 w-1 animate-pulse rounded-full bg-primary" />
            <span className="h-1 w-1 animate-pulse rounded-full bg-primary [animation-delay:150ms]" />
            <span className="h-1 w-1 animate-pulse rounded-full bg-primary [animation-delay:300ms]" />
          </span>
        )}
        <span className={cn("ml-auto text-[10px]", status.color)}>{status.label}</span>
      </button>

      {expanded && toolCall.arguments && Object.keys(toolCall.arguments).length > 0 && (
        <div className="mt-2 rounded bg-muted/50 p-2 font-mono text-[11px] text-muted-foreground">
          {Object.entries(toolCall.arguments).map(([key, value]) => (
            <div key={key} className="truncate">
              <span className="text-primary/70">{key}</span>: {JSON.stringify(value)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
