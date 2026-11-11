import { useCallback, useEffect, useRef, useState } from "react";
import type { VisualizationType } from "@/types";
import {
  Message,
  Visualization,
  QualityMetrics,
  ToolCall,
  ThinkingStep,
  ClarificationQuestion,
  SustainabilityMetrics,
  ReportSection,
  GroundingViolation,
} from "@/types";
import { generateId } from "@/lib/utils";

/**
 * Where the chat connection is. Only "open" runs queries. The two sign-in states stop
 * retrying, because no number of retries fixes them.
 */
export type ConnectionState =
  | "connecting" // first attempt
  | "authenticating" // socket open, waiting for the server to accept the token
  | "open"
  | "reconnecting" // connection lost; retrying with backoff
  | "signed_out" // the server refused the token
  | "access_expired"; // Cloudflare Access answers with its login page instead

/** Handshakes that fail in a row before checking whether Access wants a fresh login. */
const FAILED_HANDSHAKES_BEFORE_ACCESS_CHECK = 3;
/** Control messages (stop, clarification answers) kept while offline. */
const MAX_PENDING_CONTROL_MESSAGES = 20;

/**
 * True when the page's own origin answers with a login redirect or refusal: the Cloudflare
 * Access session has expired. A WebSocket can't follow that redirect, so it only ever fails.
 */
async function accessLoginRequired(): Promise<boolean> {
  try {
    const response = await fetch("/", { redirect: "manual", cache: "no-store", credentials: "same-origin" });
    return response.type === "opaqueredirect" || response.status === 401 || response.status === 403;
  } catch {
    return false;
  }
}

interface UseWebSocketOptions {
  url: string;
  authToken?: string | null;
  onMessage?: (message: Message) => void;
  /** Visualizations — streamed live during a run, or saved-job replay (replay=true). */
  onVisualization?: (visualization: Visualization, replay?: boolean) => void;
  onQualityMetrics?: (metrics: QualityMetrics) => void;
  onToolCall?: (toolCall: ToolCall) => void;
  onThinking?: (thinkingStep: ThinkingStep) => void;
  onError?: (error: Error) => void;
  onProcessGraph?: (graph: Record<string, unknown>) => void;
  onTextStreamStart?: () => void;
  onTextDelta?: (content: string) => void;
  onTextStreamEnd?: () => void;
  onSuggestions?: (suggestions: string[]) => void;
  onSessionRestored?: (sessionId: string) => void;
  onClarification?: (questions: ClarificationQuestion[], questionId?: string) => void;
  /** The pending question was answered, timed out, or failed: stop showing it. */
  onClarificationClosed?: () => void;
  onSustainabilityMetrics?: (metrics: SustainabilityMetrics) => void;
  onSavedJobCreated?: (saveId: string) => void;
  /** Report sections — streamed live during a run (live=true) or replayed when loading a saved result. */
  onReportSections?: (sections: ReportSection[], live?: boolean) => void;
  onHealthScore?: (data: Record<string, unknown>) => void;
  /** Phase D: Langfuse trace_id for the just-completed assistant response. */
  onTrace?: (traceId: string) => void;
  onGroundingWarning?: (violations: GroundingViolation[]) => void;
  onCitations?: (sources: string[]) => void;
  onReconnected?: () => void;
  onDone?: () => void;
}

interface UseWebSocketReturn {
  /** The socket is open. See connectionState for whether it is signed in. */
  isConnected: boolean;
  connectionState: ConnectionState;
  isProcessing: boolean;
  sendMessage: (content: string, extra?: Record<string, unknown>) => void;
  sendRaw: (payload: Record<string, unknown>) => void;
  reconnect: () => void;
  newSession: () => void;
  sessionId: string;
}

// Backend message types (matching web_interface.py)
interface BackendMessage {
  type:
    | "tool_start"
    | "tool_result"
    | "visualization"
    | "quality_metrics"
    | "process_graph"
    | "text"
    | "done"
    | "error"
    | "session"
    | "thinking"
    | "text_stream_start"
    | "text_delta"
    | "text_stream_end"
    | "suggestions"
    | "session_restored"
    | "clarification"
    | "sustainability_metrics"
    | "saved_job_created"
    | "report_sections"
    | "server_restart"
    | "run_resumed"
    | "usage_metrics"
    | "health_score"
    | "trace"
    | "auth_result"
    | "clarification_closed"
    | "grounding_warning"
    | "citations"
    | "specialist_delegated"
    | "method_gap"
    | "deliverable_gap"
    | "class_table_gap";
  violations?: GroundingViolation[];
  question_id?: string;
  sources?: string[];
  /** Pairs a tool's result with its start when several tools run at once. */
  tool_use_id?: string;
  authenticated?: boolean;
  /** Set on events that belong to a run; such a run always ends with its own done. */
  run_id?: string;
  code?: string;
  requeued?: boolean;
  save_id?: string;
  sections?: ReportSection[];
  live?: boolean;
  running?: boolean;
  run_active?: boolean;
  replay?: boolean;
  trace_id?: string;
  data?: Record<string, unknown>;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    total_cost_usd?: number;
    num_turns?: number;
    duration_ms?: number;
  };
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  result?: unknown;
  visualization?: unknown;
  metrics?: unknown;
  graph?: Record<string, unknown>;
  content?: string;
  suggestions?: string[];
  session_id?: string;
  thinking_type?: "analyzing" | "planning" | "executing" | "processing" | "fetching" | "validating";
  thinking_id?: string;
  thinking_completed?: boolean;
  questions?: ClarificationQuestion[];
}

const SESSION_KEY = "openeo-session-id";
const RUN_CURSOR_KEY = "openeo-run-cursor";

/** Last run event this tab has received; lets the server replay only what was missed. */
interface RunCursor {
  run_id: string;
  seq: number;
}

function readRunCursor(): RunCursor | null {
  try {
    const stored = sessionStorage.getItem(RUN_CURSOR_KEY);
    return stored ? (JSON.parse(stored) as RunCursor) : null;
  } catch {
    return null;
  }
}

function writeRunCursor(cursor: RunCursor): void {
  try {
    sessionStorage.setItem(RUN_CURSOR_KEY, JSON.stringify(cursor));
  } catch {
    // Without storage a reload replays the whole run; the in-memory cursor still dedupes.
  }
}

function getOrCreateSessionId(): string {
  const stored = localStorage.getItem(SESSION_KEY);
  if (stored) return stored;
  const id = generateId();
  localStorage.setItem(SESSION_KEY, id);
  return id;
}

export function useWebSocket({
  url,
  authToken,
  onMessage,
  onVisualization,
  onQualityMetrics,
  onToolCall,
  onThinking,
  onError,
  onProcessGraph,
  onTextStreamStart,
  onTextDelta,
  onTextStreamEnd,
  onSuggestions,
  onSessionRestored,
  onClarification,
  onClarificationClosed,
  onSustainabilityMetrics,
  onSavedJobCreated,
  onReportSections,
  onHealthScore,
  onTrace,
  onGroundingWarning,
  onCitations,
  onReconnected,
  onDone,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [isProcessing, setIsProcessing] = useState(false);
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentToolRef = useRef<{ name: string; id: string }>({
    name: "",
    id: "",
  });
  const mountedRef = useRef(true);
  const serverRestartRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const lastMessageTimeRef = useRef<number>(Date.now());
  const deadConnectionTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const runCursorRef = useRef<RunCursor | null>(readRunCursor());
  const flushedQueueRef = useRef(false);
  const failedHandshakesRef = useRef(0);
  // Stop and clarification answers made while offline, sent once the session is restored.
  const pendingControlRef = useRef<Array<Record<string, unknown>>>([]);

  // Pending message queue for messages sent while disconnected
  const pendingQueueRef = useRef<Array<{ content: string; extra?: Record<string, unknown> }>>([]);

  // Store callbacks in refs to prevent dependency issues
  const callbacksRef = useRef({
    onMessage,
    onVisualization,
    onQualityMetrics,
    onToolCall,
    onThinking,
    onError,
    onProcessGraph,
    onTextStreamStart,
    onTextDelta,
    onTextStreamEnd,
    onSuggestions,
    onSessionRestored,
    onClarification,
    onClarificationClosed,
    onSustainabilityMetrics,
    onSavedJobCreated,
    onReportSections,
    onHealthScore,
    onTrace,
    onGroundingWarning,
    onCitations,
    onReconnected,
    onDone,
  });

  // Update callbacks ref when they change
  callbacksRef.current = {
    onMessage,
    onVisualization,
    onQualityMetrics,
    onToolCall,
    onThinking,
    onError,
    onProcessGraph,
    onTextStreamStart,
    onTextDelta,
    onTextStreamEnd,
    onSuggestions,
    onSessionRestored,
    onClarification,
    onClarificationClosed,
    onSustainabilityMetrics,
    onSavedJobCreated,
    onReportSections,
    onHealthScore,
    onTrace,
    onGroundingWarning,
    onCitations,
    onReconnected,
    onDone,
  };

  const handleBackendMessage = useCallback((msg: BackendMessage) => {
    if (!mountedRef.current) return;

    const callbacks = callbacksRef.current;

    switch (msg.type) {
      case "session":
        break;

      case "thinking": {
        const thinkingStep: ThinkingStep = {
          id: msg.thinking_id || generateId(),
          type: msg.thinking_type || "processing",
          message: msg.content || "",
          timestamp: new Date(),
          completed: msg.thinking_completed,
        };
        callbacks.onThinking?.(thinkingStep);
        break;
      }

      case "tool_start": {
        const toolId = msg.tool_use_id || generateId();
        currentToolRef.current = { name: msg.tool_name || "", id: toolId };
        const toolCall: ToolCall = {
          id: toolId,
          name: msg.tool_name || "unknown",
          arguments: msg.tool_input || {},
          status: "running",
        };
        callbacks.onToolCall?.(toolCall);
        break;
      }

      case "tool_result": {
        const toolCall: ToolCall = {
          // Without an id (older servers), assume it belongs to the latest start.
          id: msg.tool_use_id || currentToolRef.current.id || generateId(),
          name: msg.tool_name || currentToolRef.current.name,
          arguments: {},
          result: msg.result,
          status: "completed",
        };
        callbacks.onToolCall?.(toolCall);
        break;
      }

      case "visualization": {
        if (msg.visualization) {
          const viz = msg.visualization as Record<string, unknown>;
          const visualization: Visualization = {
            id: generateId(),
            type: (viz.type as VisualizationType) || "map",
            title: viz.title as string,
            saveId: viz.save_id as string | undefined,
            data: (viz.data ?? {}) as Visualization["data"],
          };
          // replay=true marks saved-job replay events so they stay out of the
          // running turn's flush bundle (mirrors the report_sections live flag).
          callbacks.onVisualization?.(visualization, msg.replay === true);
        }
        break;
      }

      case "quality_metrics": {
        if (msg.metrics) {
          callbacks.onQualityMetrics?.(msg.metrics as QualityMetrics);
        }
        break;
      }

      case "process_graph": {
        if (msg.graph) {
          callbacks.onProcessGraph?.(msg.graph);
          const visualization: Visualization = {
            id: generateId(),
            type: "process_graph",
            title: "Process Graph",
            data: {
              nodes: Object.entries(msg.graph).map(([id, node]) => ({
                id,
                process_id: String((node as Record<string, unknown>).process_id || ""),
                arguments:
                  ((node as Record<string, unknown>).arguments as Record<string, unknown>) || {},
                result: Boolean((node as Record<string, unknown>).result),
              })),
              edges: [],
            },
          };
          callbacks.onVisualization?.(visualization);
        }
        break;
      }

      case "text_stream_start": {
        callbacks.onTextStreamStart?.();
        break;
      }

      case "text_delta": {
        callbacks.onTextDelta?.(msg.content || "");
        break;
      }

      case "text_stream_end": {
        callbacks.onTextStreamEnd?.();
        break;
      }

      case "text": {
        const message: Message = {
          id: generateId(),
          role: "assistant",
          content: msg.content || "",
          timestamp: new Date(),
        };
        callbacks.onMessage?.(message);
        break;
      }

      case "suggestions": {
        if (msg.suggestions) {
          callbacks.onSuggestions?.(msg.suggestions);
        }
        break;
      }

      case "session_restored": {
        if (msg.session_id) {
          // The server may keep this connection on a new session; follow it, so the next
          // reconnect restores the session that's actually in use.
          localStorage.setItem(SESSION_KEY, msg.session_id);
          callbacks.onSessionRestored?.(msg.session_id);
        }
        // Processing survives a dropped connection; this is where we learn whether
        // anything is actually still running.
        if (msg.run_active === false && !flushedQueueRef.current) setIsProcessing(false);
        break;
      }

      case "clarification": {
        if (msg.questions) {
          callbacks.onClarification?.(msg.questions, msg.question_id);
        }
        break;
      }

      case "clarification_closed": {
        // A queued offline answer is sent on reconnect before this replays, so it is the
        // server that ignores an answer whose question_id no longer matches.
        callbacks.onClarificationClosed?.();
        break;
      }

      case "sustainability_metrics": {
        if (msg.metrics) {
          callbacks.onSustainabilityMetrics?.(msg.metrics as SustainabilityMetrics);
        }
        break;
      }

      case "saved_job_created": {
        if (msg.save_id) {
          callbacks.onSavedJobCreated?.(msg.save_id as string);
        }
        break;
      }

      case "report_sections": {
        if (Array.isArray(msg.sections)) {
          // live=true marks sections streamed during this run (e.g. the
          // decision-grade Method Card) vs. saved-result replay.
          callbacks.onReportSections?.(msg.sections, msg.live === true);
        }
        break;
      }

      case "health_score": {
        if (msg.data) {
          callbacks.onHealthScore?.(msg.data);
        }
        break;
      }

      case "trace": {
        // Phase D: Langfuse trace_id for the assistant response.
        // Forward to the chat panel so it can attach thumbs feedback.
        if (msg.trace_id) {
          callbacks.onTrace?.(msg.trace_id);
        }
        break;
      }

      case "done": {
        setIsProcessing(false);
        callbacks.onDone?.();
        break;
      }

      case "auth_result": {
        if (msg.authenticated) {
          setConnectionState("open");
        } else {
          setConnectionState("signed_out");
          setIsProcessing(false);
          callbacks.onError?.(new Error("Your sign-in was refused. Sign in again to continue."));
        }
        break;
      }

      case "error": {
        // A run's error is followed by that run's done, which ends the turn. Other errors
        // (rate limit, sign-in) answer a message that never started a run. A failed Load on
        // Map answers a map request, not a message, so it never ends a turn either.
        const queuedForLater = msg.code === "auth_expired" && msg.requeued;
        const notAboutTheTurn = msg.code === "saved_job_unavailable";
        if (!msg.run_id && !queuedForLater && !notAboutTheTurn) setIsProcessing(false);
        callbacks.onError?.(new Error(msg.content || "Unknown error"));
        break;
      }

      case "usage_metrics": {
        if (msg.usage) {
          console.log(
            "[WebSocket] Usage:",
            msg.usage.input_tokens,
            "in,",
            msg.usage.output_tokens,
            "out, cost:",
            msg.usage.total_cost_usd
          );
        }
        break;
      }

      case "run_resumed": {
        // ws.onclose cleared this; the run kept going while we were away.
        if (msg.running) setIsProcessing(true);
        break;
      }

      case "server_restart": {
        console.log(
          "[WebSocket] Server restart notification received, will reconnect automatically"
        );
        serverRestartRef.current = true;
        break;
      }

      case "grounding_warning": {
        if (msg.violations?.length) callbacks.onGroundingWarning?.(msg.violations);
        break;
      }

      case "citations": {
        if (msg.sources?.length) callbacks.onCitations?.(msg.sources);
        break;
      }

      // Telemetry for traces and evals. When enforcement is on, the bridge streams the
      // user-facing notice as text, so there is nothing to show from these.
      case "specialist_delegated":
      case "method_gap":
      case "deliverable_gap":
      case "class_table_gap":
        break;

      default:
        // A type this client doesn't know: say so rather than drop it silently.
        console.debug("[WebSocket] Ignored unknown event type:", (msg as { type?: string }).type);
    }
  }, []);

  const connect = useCallback(() => {
    // Don't connect if unmounted
    if (!mountedRef.current) return;

    // Don't connect if already connected or connecting
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    setConnectionState((current) => (current === "connecting" ? "connecting" : "reconnecting"));
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const baseWsUrl = url.startsWith("ws") ? url : `${protocol}//${window.location.host}${url}`;
    // Security: never send auth token in query string (leaks to logs/Referer).
    // Authenticate via message after connection instead.
    const wsUrl = baseWsUrl;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      let opened = false;

      ws.onopen = () => {
        if (!mountedRef.current) {
          ws.close();
          return;
        }
        opened = true;
        failedHandshakesRef.current = 0;
        serverRestartRef.current = false;
        setIsConnected(true);
        setConnectionState(authToken ? "authenticating" : "open");
        const attemptsBeforeReset = reconnectAttemptsRef.current;
        reconnectAttemptsRef.current = 0; // Reset backoff on successful connect
        lastMessageTimeRef.current = Date.now();

        // Authenticate via message-based flow (not query param)
        if (authToken) {
          ws.send(JSON.stringify({ type: "authenticate", token: authToken }));
        }

        // Attempt to restore previous session
        const storedId = localStorage.getItem(SESSION_KEY);
        if (storedId) {
          const cursor = runCursorRef.current;
          ws.send(
            JSON.stringify({
              type: "restore_session",
              session_id: storedId,
              ...(cursor ? { run_id: cursor.run_id, last_seq: cursor.seq } : {}),
            })
          );
        }

        // Control messages first: a stop or an answer refers to what was already running.
        for (const control of pendingControlRef.current.splice(0)) {
          ws.send(JSON.stringify(control));
        }

        // Flush queued messages that were sent while disconnected
        const queued = pendingQueueRef.current.splice(0);
        flushedQueueRef.current = queued.length > 0;
        for (const msg of queued) {
          ws.send(
            JSON.stringify({
              type: "message",
              content: msg.content,
              session_id: storedId || sessionId,
              ...msg.extra,
            })
          );
        }

        // If this is a reconnect (not initial connect), notify consumers
        if (attemptsBeforeReset > 0) {
          callbacksRef.current.onReconnected?.();
        }
      };

      ws.onclose = (event) => {
        if (!mountedRef.current) return;

        setIsConnected(false);
        wsRef.current = null;

        if (mountedRef.current && event.code !== 1000) {
          const scheduleRetry = () => {
            const isRestart = serverRestartRef.current;
            serverRestartRef.current = false;
            // Exponential backoff: 500ms, 1s, 2s, 4s, 8s, ... max 30s. A server restart or
            // an expired sign-in (4401, the client signs in again) retries at once.
            const attempt = reconnectAttemptsRef.current;
            const delay =
              isRestart || event.code === 4401 ? 500 : Math.min(500 * Math.pow(2, attempt), 30000);
            reconnectAttemptsRef.current = attempt + 1;
            setConnectionState("reconnecting");
            console.log(`[WebSocket] Reconnecting in ${delay}ms (attempt ${attempt + 1})`);
            reconnectTimeoutRef.current = setTimeout(() => {
              if (mountedRef.current) {
                connect();
              }
            }, delay);
          };

          if (!opened) failedHandshakesRef.current += 1;
          if (failedHandshakesRef.current >= FAILED_HANDSHAKES_BEFORE_ACCESS_CHECK) {
            failedHandshakesRef.current = 0;
            void accessLoginRequired().then((required) => {
              if (!mountedRef.current) return;
              if (required) {
                // Retrying can't succeed until the page reloads through the login.
                setConnectionState("access_expired");
                setIsProcessing(false);
              } else {
                scheduleRetry();
              }
            });
          } else {
            scheduleRetry();
          }
        }
      };

      ws.onerror = (event) => {
        console.error("[WebSocket] Error:", event);
        // onclose will handle cleanup
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        lastMessageTimeRef.current = Date.now();
        try {
          const parsed = JSON.parse(event.data);
          // Respond to server ping to keep connection alive
          if (parsed.type === "ping") {
            ws.send(JSON.stringify({ type: "pong" }));
            return;
          }
          if (typeof parsed.seq === "number" && typeof parsed.run_id === "string") {
            const cursor = runCursorRef.current;
            if (cursor && cursor.run_id === parsed.run_id && parsed.seq <= cursor.seq) return;
            runCursorRef.current = { run_id: parsed.run_id, seq: parsed.seq };
            writeRunCursor(runCursorRef.current);
          }
          const backendMessage: BackendMessage = parsed;
          handleBackendMessage(backendMessage);
        } catch (error) {
          console.error("[WebSocket] Failed to parse message:", error);
        }
      };
    } catch (error) {
      console.error("[WebSocket] Failed to create connection:", error);
    }
  }, [url, authToken, sessionId, handleBackendMessage]);

  const sendMessage = useCallback(
    (content: string, extra?: Record<string, unknown>) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        setIsProcessing(true);
        const payload = {
          type: "message",
          content,
          session_id: sessionId,
          ...extra,
        };
        wsRef.current.send(JSON.stringify(payload));
      } else {
        // Queue message for delivery on reconnect
        pendingQueueRef.current.push({ content, extra });
        setIsProcessing(true);
        console.log("[WebSocket] Message queued for delivery on reconnect:", content.slice(0, 80));
      }
    },
    [sessionId]
  );

  const sendRaw = useCallback((payload: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    } else if (pendingControlRef.current.length < MAX_PENDING_CONTROL_MESSAGES) {
      // Sent once reconnected, so a stop or an answer made offline isn't silently lost.
      pendingControlRef.current.push(payload);
    }
  }, []);

  const newSession = useCallback(() => {
    // Generate fresh session ID and clear stored one
    const newId = generateId();
    localStorage.setItem(SESSION_KEY, newId);
    setSessionId(newId);
    // Nothing queued, pending or remembered belongs to the new session
    pendingQueueRef.current = [];
    pendingControlRef.current = [];
    runCursorRef.current = null;
    try {
      sessionStorage.removeItem(RUN_CURSOR_KEY);
    } catch {
      // Storage unavailable; the in-memory cursor is cleared.
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    // Reconnect with the new session
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
    setIsProcessing(false);
    setTimeout(() => {
      if (mountedRef.current) {
        connect();
      }
    }, 100);
  }, [connect]);

  const reconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (connectTimeoutRef.current) {
      clearTimeout(connectTimeoutRef.current);
      connectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
    setTimeout(() => {
      if (mountedRef.current) {
        connect();
      }
    }, 100);
  }, [connect]);

  useEffect(() => {
    mountedRef.current = true;

    // Small delay to handle React StrictMode double-mount
    connectTimeoutRef.current = setTimeout(() => {
      if (mountedRef.current) {
        connect();
      }
    }, 100);

    // Dead connection detector: if no message received in 90s
    // (backend pings every 25s), force reconnect.
    // Generous timeout to avoid false disconnects during heavy tool execution.
    deadConnectionTimerRef.current = setInterval(() => {
      if (!mountedRef.current) return;
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        const elapsed = Date.now() - lastMessageTimeRef.current;
        if (elapsed > 90000) {
          console.log(`[WebSocket] No message in ${Math.round(elapsed / 1000)}s, reconnecting`);
          ws.close(4000, "Dead connection detected");
        }
      }
    }, 10000);

    // Backoff timers are throttled in background tabs and useless while offline,
    // so retry as soon as the network or the tab comes back.
    const reconnectNow = () => {
      if (document.visibilityState === "hidden") return;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      connect();
    };
    window.addEventListener("online", reconnectNow);
    document.addEventListener("visibilitychange", reconnectNow);

    return () => {
      mountedRef.current = false;
      window.removeEventListener("online", reconnectNow);
      document.removeEventListener("visibilitychange", reconnectNow);

      if (connectTimeoutRef.current) {
        clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (deadConnectionTimerRef.current) {
        clearInterval(deadConnectionTimerRef.current);
        deadConnectionTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close(1000, "Component unmounting");
        wsRef.current = null;
      }
    };
  }, [connect]);

  return {
    isConnected,
    connectionState,
    isProcessing,
    sendMessage,
    sendRaw,
    reconnect,
    newSession,
    sessionId,
  };
}
