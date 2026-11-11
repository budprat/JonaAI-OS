/**
 * useJonaChat — High-level chat state management hook.
 *
 * Layers on top of useWebSocket (transport unchanged) to consolidate
 * all chat state (messages, visualizations, thinking, metrics, etc.)
 * into a single hook. Returns backward-compatible derived state so
 * existing components work unchanged.
 *
 * Architecture:
 *   useJonaChat (state management, UIMessage building)
 *     ├── useWebSocket (WebSocket transport, message parsing)
 *     └── useConversationHistory (localStorage persistence)
 */
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useWebSocket, type ConnectionState } from "./useWebSocket";
import { useConversationHistory } from "./useConversationHistory";
import type { UseConversationHistoryReturn } from "./useConversationHistory";
import { toast } from "@/hooks/use-toast";
import type {
  GroundingViolation,
  Message,
  Visualization,
  QualityMetrics,
  SustainabilityMetrics,
  ToolCall,
  ThinkingStep,
  ClarificationQuestion,
  UIMessage,
  ReportSection,
} from "@/types";
import { generateId } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UseJonaChatOptions {
  url: string;
  /** Auth token to pass to WebSocket for authentication. */
  authToken?: string | null;
  /** Called when a tool call starts or completes (for workflow tracking). */
  onToolCall?: (toolCall: ToolCall) => void;
  /** Called when the assistant turn is complete. */
  onDone?: () => void;
  /** Called when an error occurs. */
  onError?: (error: Error) => void;
  /** Called when a new result is auto-saved (for refreshing saved results panel). */
  onSavedJobCreated?: (saveId: string) => void;
  /** Injectable transport hook (defaults to useWebSocket). Enables DI for testing. */
  useTransport?: typeof useWebSocket;
  /** Injectable conversation history hook (defaults to useConversationHistory). Enables DI for testing. */
  useHistory?: typeof useConversationHistory;
}

interface UseJonaChatReturn {
  // --- AI SDK v5-style (future consumers) ---
  uiMessages: UIMessage[];

  // --- Backward-compatible derived state ---
  messages: Message[];
  visualizations: Visualization[];
  thinkingSteps: ThinkingStep[];
  qualityMetrics: QualityMetrics | null;
  sustainabilityMetrics: SustainabilityMetrics | null;
  insightData: Record<string, unknown> | null;
  processGraph: Record<string, unknown> | null;
  contextSuggestions: string[];
  pendingClarification: ClarificationQuestion[] | null;
  /** Accumulated report sections from analysis tools (report, interpretation, Earth-Agent). */
  reportSections: ReportSection[];

  // --- Controls ---
  isConnected: boolean;
  connectionState: ConnectionState;
  isProcessing: boolean;
  /** Send a user message. displayContent overrides what shows in chat (e.g., omit bbox prefix). */
  sendMessage: (content: string, extra?: Record<string, unknown>, displayContent?: string) => void;
  sendRaw: (payload: Record<string, unknown>) => void;
  reconnect: () => void;
  newSession: () => void;
  sessionId: string;
  clearChat: () => void;
  appendMessage: (message: Message) => void;
  submitClarification: (answers: Record<string, string | string[]>) => void;
  removeVisualizationBySaveId: (saveId: string) => void;
  removeVisualization: (vizId: string) => void;
  /** Re-insert a previously removed visualization (for undo support). */
  restoreVisualization: (viz: Visualization) => void;
  /** Retry the last failed query. */
  retryLastQuery: () => void;

  // --- Conversation History ---
  /** Conversation history manager for the sidebar. */
  conversationHistory: UseConversationHistoryReturn;
  /** Load a previous conversation's messages into the chat. */
  loadConversation: (id: string) => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useJonaChat(options: UseJonaChatOptions): UseJonaChatReturn {
  // ---- Internal state ----
  const [messages, setMessages] = useState<Message[]>([]);
  const [visualizations, setVisualizations] = useState<Visualization[]>([]);
  const [qualityMetrics, setQualityMetrics] = useState<QualityMetrics | null>(null);
  const [insightData, setInsightData] = useState<Record<string, unknown> | null>(null);
  const [sustainabilityMetrics, setSustainabilityMetrics] = useState<SustainabilityMetrics | null>(
    null
  );
  const [processGraph, setProcessGraph] = useState<Record<string, unknown> | null>(null);
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
  const [contextSuggestions, setContextSuggestions] = useState<string[]>([]);
  const [pendingClarification, setPendingClarification] = useState<ClarificationQuestion[] | null>(
    null
  );
  const [activeToolCalls, setActiveToolCalls] = useState<ToolCall[]>([]);
  const [reportSections, setReportSections] = useState<ReportSection[]>([]);

  const streamingMsgIdRef = useRef<string | null>(null);

  // rAF-batched text delta accumulator refs — collapses 50+/s WebSocket
  // text_delta events into ~60/s React state updates (one per animation frame).
  const pendingDeltaRef = useRef("");
  const rafRef = useRef<number | null>(null);

  // Track last query for retry support
  const lastQueryRef = useRef<{
    content: string;
    extra?: Record<string, unknown>;
  } | null>(null);

  // Keep external callbacks current without triggering re-renders
  const optionsRef = useRef(options);
  optionsRef.current = options;

  // ---- Conversation History (injectable for testing) ----
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const historyHook = useMemo(() => options.useHistory ?? useConversationHistory, []);
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const history = historyHook();

  // Refs for debounced auto-save (avoid stale closures)
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const visualizationsRef = useRef(visualizations);
  visualizationsRef.current = visualizations;
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Run-scoped buffers: viz + report sections produced during the current turn.
  // Reset on each sendMessage; flushed to the saved result(s) on turn completion
  // so "Load on Map" repopulates Charts/Table/Report/Process tabs.
  const runVizRef = useRef<Visualization[]>([]);
  const runReportRef = useRef<ReportSection[]>([]);
  // Source URLs this turn's tools reported, attached to its answer when the turn ends.
  const runSourcesRef = useRef<string[]>([]);

  // ---- Restore persisted conversation on mount ----
  const hasRestoredRef = useRef(false);
  useEffect(() => {
    if (hasRestoredRef.current) return;
    hasRestoredRef.current = true;

    const activeId = history.activeConversationId;
    if (activeId) {
      const restored = history.loadConversation(activeId);
      if (restored && restored.length > 0) {
        setMessages(restored);
      }
    }
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- Debounced auto-save (1.5s after last message change) ----
  useEffect(() => {
    // Skip saving during streaming (will save on finalize/done)
    if (streamingMsgIdRef.current) return;

    // Only save if there are meaningful messages
    const hasMeaningful = messages.some((m) => m.role === "user" || m.role === "assistant");
    if (!hasMeaningful) return;

    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }

    saveTimerRef.current = setTimeout(() => {
      history.saveConversation(messagesRef.current, visualizationsRef.current);
    }, 1500);

    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length]);

  // ---- Cleanup rAF on unmount ----
  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // ---- Streaming lifecycle ----

  const finalizeStreaming = useCallback(() => {
    const id = streamingMsgIdRef.current;
    if (!id) return;

    // Flush any pending delta before finalizing so no text is lost
    if (pendingDeltaRef.current) {
      const remaining = pendingDeltaRef.current;
      pendingDeltaRef.current = "";
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, content: m.content + remaining } : m))
      );
    }

    setMessages((prev) => {
      const msg = prev.find((m) => m.id === id);
      if (msg && !msg.content.trim()) {
        // Remove empty streaming messages
        return prev.filter((m) => m.id !== id);
      }
      return prev.map((m) => (m.id === id ? { ...m, isStreaming: false } : m));
    });
    streamingMsgIdRef.current = null;
  }, []);

  // ---- WebSocket callbacks (all stable via useCallback) ----

  const handleTextStreamStart = useCallback(() => {
    const id = generateId();
    streamingMsgIdRef.current = id;
    setMessages((prev) => [
      ...prev,
      {
        id,
        role: "assistant" as const,
        content: "",
        timestamp: new Date(),
        isStreaming: true,
      },
    ]);
  }, []);

  const handleTextDelta = useCallback((content: string) => {
    // After a page reload the run resumes mid-message, with no stream start to replay.
    if (!streamingMsgIdRef.current) handleTextStreamStart();
    pendingDeltaRef.current += content;
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      const accumulated = pendingDeltaRef.current;
      pendingDeltaRef.current = "";
      rafRef.current = null;
      const id = streamingMsgIdRef.current;
      if (!id) return;
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, content: m.content + accumulated } : m))
      );
    });
  }, [handleTextStreamStart]);

  const handleTextStreamEnd = useCallback(() => {
    finalizeStreaming();
  }, [finalizeStreaming]);

  const handleMessage = useCallback((message: Message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  /** Live viz join the run's flush bundle; saved-job replays render only —
   *  bundling them would cross-contaminate saved results (both directions). */
  const handleVisualization = useCallback((viz: Visualization, replay?: boolean) => {
    if (!replay) runVizRef.current.push(viz);
    setVisualizations((prev) => [...prev, viz]);
  }, []);

  /** Report sections streamed live during a run (e.g. the decision-grade
   *  Method Card) or replayed when loading a saved result (load_saved_job). */
  const handleReportSections = useCallback((sections: ReportSection[], live?: boolean) => {
    if (!Array.isArray(sections) || sections.length === 0) return;
    const normalized = sections.map((s) => ({
      ...s,
      id: s.id ?? generateId(),
      timestamp: s.timestamp ? new Date(s.timestamp) : new Date(),
    }));
    // Live sections belong to this run's bundle so they persist with the
    // saved result on done; replayed ones must not be re-persisted.
    if (live) runReportRef.current.push(...normalized);
    setReportSections((prev) => [...prev, ...normalized]);
  }, []);

  const handleQualityMetrics = useCallback((metrics: QualityMetrics) => {
    setQualityMetrics(metrics);
  }, []);

  const handleSustainabilityMetrics = useCallback((metrics: SustainabilityMetrics) => {
    setSustainabilityMetrics(metrics);
  }, []);

  const handleHealthScore = useCallback((data: Record<string, unknown>) => {
    setInsightData(data);
  }, []);

  /**
   * Phase D: attach the Langfuse trace_id to the last assistant message.
   * The backend emits a `trace` WS event after _run_query completes. We
   * stamp it onto whatever assistant message was most recently added so
   * ChatMessage can use it for thumbs-up/down scoring.
   */
  // Update this turn's answer: the last assistant message after the latest user message.
  // An earlier turn's message is never touched.
  const updateTurnAnswer = useCallback((update: (msg: Message) => Message) => {
    setMessages((prev) => {
      for (let i = prev.length - 1; i >= 0; i--) {
        const msg = prev[i];
        if (!msg || msg.role === "user") return prev;
        if (msg.role === "assistant") {
          const next = [...prev];
          next[i] = update(msg);
          return next;
        }
      }
      return prev;
    });
  }, []);

  const handleGroundingWarning = useCallback(
    (violations: GroundingViolation[]) => {
      updateTurnAnswer((msg) => ({ ...msg, groundingWarnings: violations }));
    },
    [updateTurnAnswer],
  );

  const handleCitations = useCallback((sources: string[]) => {
    for (const source of sources) {
      if (!runSourcesRef.current.includes(source)) runSourcesRef.current.push(source);
    }
  }, []);

  const handleTrace = useCallback((traceId: string) => {
    setMessages((prev) => {
      // Find the most recent assistant message and set its traceId
      for (let i = prev.length - 1; i >= 0; i--) {
        const msg = prev[i];
        if (msg && msg.role === "assistant") {
          const next = [...prev];
          next[i] = { ...msg, traceId };
          return next;
        }
      }
      return prev;
    });
  }, []);

  const handleProcessGraph = useCallback((graph: Record<string, unknown>) => {
    setProcessGraph(graph);
  }, []);

  const handleThinking = useCallback((step: ThinkingStep) => {
    setThinkingSteps((prev) => {
      if (step.completed) return prev.filter((s) => s.id !== step.id);
      // Keep only the last 3 active steps to avoid accumulation
      const active = [...prev.filter((s) => !s.completed && s.id !== step.id), step];
      return active.slice(-3);
    });
  }, []);

  const handleSuggestions = useCallback((suggestions: string[]) => {
    setContextSuggestions(suggestions);
  }, []);

  const handleSessionRestored = useCallback((_sessionId: string) => {
    // Session restored — no action needed beyond transport-level handling
  }, []);

  // The open question's id, echoed with the answer so the server can tell which question
  // it answers.
  const questionIdRef = useRef<string | undefined>(undefined);

  const handleClarification = useCallback((questions: ClarificationQuestion[], questionId?: string) => {
    questionIdRef.current = questionId;
    setPendingClarification(questions);
  }, []);

  const handleClarificationClosed = useCallback(() => {
    setPendingClarification(null);
  }, []);

  // Tools whose results are routed to the Report panel instead of chat
  const REPORT_TOOLS: Record<string, ReportSection["type"]> = {
    generate_report: "report",
    interpret_result: "interpretation",
    earth_agent_analyze: "earth_agent",
    agentic_decision_query: "report",
  };

  const handleToolCall = useCallback((toolCall: ToolCall) => {
    if (toolCall.status === "running") {
      // Track active tool calls for ToolCallPart rendering
      setActiveToolCalls((prev) => [...prev, toolCall]);
    } else if (toolCall.status === "completed") {
      // Remove from active
      setActiveToolCalls((prev) => prev.filter((tc) => tc.id !== toolCall.id));

      if (toolCall.result != null) {
        const reportType = REPORT_TOOLS[toolCall.name];

        if (reportType) {
          // Route to Report panel instead of chat
          const resultText =
            typeof toolCall.result === "string"
              ? toolCall.result
              : JSON.stringify(toolCall.result, null, 2);

          const reportSection: ReportSection = {
            id: generateId(),
            type: reportType,
            title:
              toolCall.name === "generate_report"
                ? "Analysis Report"
                : toolCall.name === "interpret_result"
                  ? "Interpretation"
                  : toolCall.name === "agentic_decision_query"
                    ? "Decision Report"
                    : "Earth-Agent Analysis",
            content: resultText,
            timestamp: new Date(),
            toolName: toolCall.name,
          };
          runReportRef.current.push(reportSection);
          setReportSections((prev) => [...prev, reportSection]);

          // Show compact notification in chat instead of full result
          setMessages((prev) => [
            ...prev,
            {
              id: generateId(),
              role: "tool" as const,
              content: "",
              timestamp: new Date(),
              toolName: toolCall.name,
              toolResult: { _reportRouted: true, type: reportType },
            },
          ]);
        } else {
          // Normal tool result — show in chat
          setMessages((prev) => [
            ...prev,
            {
              id: generateId(),
              role: "tool" as const,
              content: "",
              timestamp: new Date(),
              toolName: toolCall.name,
              toolResult: toolCall.result,
            },
          ]);
        }
      }
    } else if (toolCall.status === "error") {
      setActiveToolCalls((prev) => prev.filter((tc) => tc.id !== toolCall.id));
    }
    // Forward to consumer for workflow tracking
    optionsRef.current.onToolCall?.(toolCall);
  }, []);

  const handleDone = useCallback(() => {
    finalizeStreaming();
    if (runSourcesRef.current.length > 0) {
      const sources = runSourcesRef.current;
      runSourcesRef.current = [];
      updateTurnAnswer((msg) => ({ ...msg, sources }));
    }
    setThinkingSteps([]);
    setActiveToolCalls([]);
    // Nothing can answer a question once its turn is over.
    setPendingClarification(null);
    // Save conversation immediately on turn completion
    setTimeout(() => {
      history.saveConversation(messagesRef.current, visualizationsRef.current);
    }, 0);
    // Persist this turn's non-map viz + report sections to the saved result(s) so
    // "Load on Map" later repopulates Charts/Table/Report/Process tabs. Maps reload
    // from the GeoTIFF, so they are excluded from the bundle.
    const BUNDLE_TYPES = new Set(["chart", "table", "process_graph", "classification"]);
    const saveIds = Array.from(
      new Set(runVizRef.current.map((v) => v.saveId).filter((id): id is string => Boolean(id)))
    );
    const bundleViz = runVizRef.current.filter((v) => BUNDLE_TYPES.has(v.type));
    const bundleReport = runReportRef.current;
    if (saveIds.length > 0 && (bundleViz.length > 0 || bundleReport.length > 0)) {
      const token = optionsRef.current.authToken;
      const body = JSON.stringify({ visualizations: bundleViz, reportSections: bundleReport });
      for (const sid of saveIds) {
        fetch(`/saved-jobs/${sid}/visualizations`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body,
        }).catch(() => {
          /* best-effort; tab reload is a nicety, not critical */
        });
      }
    }
    optionsRef.current.onDone?.();
  }, [finalizeStreaming, history.saveConversation, updateTurnAnswer]);

  const handleError = useCallback(
    (error: Error) => {
      finalizeStreaming();
      setThinkingSteps([]);
      setActiveToolCalls([]);
      setMessages((prev) => [
        ...prev,
        {
          id: generateId(),
          role: "system" as const,
          content: `Error: ${error.message}`,
          timestamp: new Date(),
          isRetryable: true,
        },
      ]);
      toast({
        title: "Error",
        description: error.message,
        variant: "destructive",
      });
      optionsRef.current.onError?.(error);
    },
    [finalizeStreaming]
  );

  const handleReconnected = useCallback(() => {
    toast({ title: "Reconnected", description: "Connection restored" });
  }, []);

  // ---- Transport (injectable for testing via useTransport) ----
  // NOTE: useTransport must be stable across renders (same hook identity).
  // This is safe because: in production it's always useWebSocket, in tests
  // it's a fixed function passed once. We memoize to satisfy rules-of-hooks.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const transport = useMemo(() => options.useTransport ?? useWebSocket, []);
  const ws = transport({
    url: options.url,
    authToken: options.authToken,
    onMessage: handleMessage,
    onVisualization: handleVisualization,
    onQualityMetrics: handleQualityMetrics,
    onToolCall: handleToolCall,
    onThinking: handleThinking,
    onError: handleError,
    onProcessGraph: handleProcessGraph,
    onTextStreamStart: handleTextStreamStart,
    onTextDelta: handleTextDelta,
    onTextStreamEnd: handleTextStreamEnd,
    onSuggestions: handleSuggestions,
    onSessionRestored: handleSessionRestored,
    onClarification: handleClarification,
    onClarificationClosed: handleClarificationClosed,
    onSustainabilityMetrics: handleSustainabilityMetrics,
    onHealthScore: handleHealthScore,
    onTrace: handleTrace,
    onGroundingWarning: handleGroundingWarning,
    onCitations: handleCitations,
    onSavedJobCreated: options.onSavedJobCreated,
    onReportSections: handleReportSections,
    onReconnected: handleReconnected,
    onDone: handleDone,
  });

  // Finalize streaming when processing ends. A dropped connection doesn't end it:
  // the run continues on the server and its reply resumes in the same message.
  const prevProcessingRef = useRef(false);
  useEffect(() => {
    if (prevProcessingRef.current && !ws.isProcessing) {
      finalizeStreaming();
    }
    prevProcessingRef.current = ws.isProcessing;
  }, [ws.isProcessing, finalizeStreaming]);

  // ---- Public API ----

  const sendMessage = useCallback(
    (content: string, extra?: Record<string, unknown>, displayContent?: string) => {
      // Store query for retry support
      lastQueryRef.current = { content, extra };

      // Reset run-scoped buffers for the new turn
      runVizRef.current = [];
      runReportRef.current = [];
      runSourcesRef.current = [];

      setThinkingSteps([]);
      setContextSuggestions([]);
      setMessages((prev) => [
        ...prev,
        {
          id: generateId(),
          role: "user" as const,
          content: displayContent ?? content,
          timestamp: new Date(),
        },
      ]);
      ws.sendMessage(content, extra);
    },
    [ws.sendMessage]
  );

  const retryLastQuery = useCallback(() => {
    if (lastQueryRef.current) {
      sendMessage(lastQueryRef.current.content, lastQueryRef.current.extra);
    }
  }, [sendMessage]);

  const clearChat = useCallback(() => {
    setMessages([]);
    setVisualizations([]);
    setQualityMetrics(null);
    setSustainabilityMetrics(null);
    setProcessGraph(null);
    setThinkingSteps([]);
    setActiveToolCalls([]);
    setContextSuggestions([]);
    setPendingClarification(null);
    setReportSections([]);
    lastQueryRef.current = null;
    ws.newSession();
  }, [ws.newSession]);

  const appendMessage = useCallback((message: Message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  const removeVisualizationBySaveId = useCallback((saveId: string) => {
    setVisualizations((prev) => prev.filter((v) => v.saveId !== saveId));
  }, []);

  const removeVisualization = useCallback((vizId: string) => {
    setVisualizations((prev) => prev.filter((v) => v.id !== vizId));
  }, []);

  const restoreVisualization = useCallback((viz: Visualization) => {
    setVisualizations((prev) => {
      // Avoid duplicate if already present
      if (prev.some((v) => v.id === viz.id)) return prev;
      return [...prev, viz];
    });
  }, []);

  const submitClarification = useCallback(
    (answers: Record<string, string | string[]>) => {
      setPendingClarification(null);
      const questionId = questionIdRef.current;
      ws.sendRaw({
        type: "clarification_response",
        answers,
        ...(questionId ? { question_id: questionId } : {}),
      });
    },
    [ws.sendRaw]
  );

  /** Load a previous conversation's messages into the active chat. */
  const loadConversation = useCallback(
    (id: string) => {
      const restored = history.loadConversation(id);
      if (!restored) return;

      // Clear current state
      setMessages(restored);
      setVisualizations([]);
      setQualityMetrics(null);
      setSustainabilityMetrics(null);
      setProcessGraph(null);
      setThinkingSteps([]);
      setActiveToolCalls([]);
      setContextSuggestions([]);
      setPendingClarification(null);
      lastQueryRef.current = null;

      // Set as active conversation
      history.setActiveConversation(id);

      // Start new WebSocket session for continued conversation
      ws.newSession();
    },
    [history.loadConversation, history.setActiveConversation, ws.newSession]
  );

  // ---- AI SDK v5 UIMessage derivation ----

  const uiMessages = useMemo((): UIMessage[] => {
    const lastMsgIndex = messages.length - 1;

    return messages.map((msg, idx): UIMessage => {
      // Tool messages -> ToolResultPart
      if (msg.role === "tool") {
        return {
          id: msg.id,
          role: msg.role,
          content: msg.content,
          parts: [
            {
              type: "tool-result" as const,
              toolCallId: msg.id,
              toolName: msg.toolName || "unknown",
              output: msg.toolResult,
            },
          ],
          createdAt: msg.timestamp,
        };
      }

      // Build parts array starting with text content
      const parts: UIMessage["parts"] = [];
      if (msg.content) {
        parts.push({ type: "text" as const, text: msg.content });
      }

      // Assistant messages: include inline toolCalls if present (backward compat)
      if (msg.role === "assistant" && msg.toolCalls) {
        for (const tc of msg.toolCalls) {
          parts.push({
            type: "tool-call" as const,
            toolCallId: tc.id,
            toolName: tc.name,
            input: tc.arguments,
            state:
              tc.status === "running"
                ? "running"
                : tc.status === "completed"
                  ? "result"
                  : tc.status === "error"
                    ? "error"
                    : "call",
          });
        }
      }

      // Last assistant message: append activeToolCalls as ToolCallParts
      if (msg.role === "assistant" && idx === lastMsgIndex) {
        for (const tc of activeToolCalls) {
          parts.push({
            type: "tool-call" as const,
            toolCallId: tc.id,
            toolName: tc.name,
            input: tc.arguments,
            state: "running",
          });
        }
      }

      // Build metadata only if there's something to attach (Phase D: traceId)
      const metaParts: {
        isStreaming?: boolean;
        traceId?: string;
        groundingWarnings?: GroundingViolation[];
        sources?: string[];
      } = {};
      if (msg.isStreaming) metaParts.isStreaming = true;
      if (msg.traceId) metaParts.traceId = msg.traceId;
      if (msg.groundingWarnings?.length) metaParts.groundingWarnings = msg.groundingWarnings;
      if (msg.sources?.length) metaParts.sources = msg.sources;
      const metadata = Object.keys(metaParts).length > 0 ? metaParts : undefined;

      return {
        id: msg.id,
        role: msg.role,
        content: msg.content,
        parts,
        createdAt: msg.timestamp,
        metadata,
      };
    });
  }, [messages, activeToolCalls]);

  // ---- Return ----

  return {
    uiMessages,
    messages,
    visualizations,
    thinkingSteps,
    qualityMetrics,
    sustainabilityMetrics,
    insightData,
    processGraph,
    contextSuggestions,
    pendingClarification,
    reportSections,
    isConnected: ws.isConnected,
    connectionState: ws.connectionState,
    isProcessing: ws.isProcessing,
    sendMessage,
    sendRaw: ws.sendRaw,
    reconnect: ws.reconnect,
    newSession: ws.newSession,
    sessionId: ws.sessionId,
    clearChat,
    appendMessage,
    submitClarification,
    removeVisualizationBySaveId,
    removeVisualization,
    restoreVisualization,
    retryLastQuery,
    conversationHistory: history,
    loadConversation,
  };
}
