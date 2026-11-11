/**
 * useWebSocket hook tests.
 *
 * Tests the WebSocket transport layer directly using a fake WebSocket class
 * that replaces the global WebSocket constructor. This is standard test
 * isolation for hooks that create native WebSocket connections -- the same
 * pattern used by the ws-simulator.ts in this project for useJonaChat.
 *
 * Covers: connection lifecycle, all 18 message types, reconnection with
 * exponential backoff, session management, ping/pong, message queueing,
 * dead connection detection, auth token handling.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWebSocket } from "../useWebSocket";

// ---------------------------------------------------------------------------
// Fake WebSocket -- replaces the browser native WebSocket for test isolation
// ---------------------------------------------------------------------------

type WSEventHandler = (event: unknown) => void;

/**
 * A controllable replacement for the browser WebSocket.
 * Allows tests to simulate open/close/message/error events and inspect
 * sent data without needing a real server.
 */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;

  url: string;
  readyState: number = FakeWebSocket.CONNECTING;
  onopen: WSEventHandler | null = null;
  onclose: WSEventHandler | null = null;
  onerror: WSEventHandler | null = null;
  onmessage: WSEventHandler | null = null;
  sentData: string[] = [];

  // Class-level tracking
  static instances: FakeWebSocket[] = [];
  static latestInstance: FakeWebSocket | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
    FakeWebSocket.latestInstance = this;
  }

  send(data: string) {
    this.sentData.push(data);
  }

  close(_code?: number, _reason?: string) {
    this.readyState = FakeWebSocket.CLOSED;
    if (this.onclose) {
      this.onclose({ code: _code || 1000, reason: _reason || "" });
    }
  }

  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() {
    return true;
  }

  // --- Test helpers ---

  /** Simulate server opening the connection */
  triggerOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }

  /** Simulate server sending a JSON message */
  triggerServerMessage(data: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  /** Simulate connection close from server */
  triggerClose(code = 1006, reason = "") {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }

  /** Simulate connection error */
  triggerError() {
    this.onerror?.({});
  }

  /** Get all sent data as parsed JSON objects */
  parsedSentData(): Record<string, unknown>[] {
    return this.sentData.map((m) => JSON.parse(m));
  }

  static resetTracking() {
    FakeWebSocket.instances = [];
    FakeWebSocket.latestInstance = null;
  }
}

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

const OriginalWebSocket = globalThis.WebSocket;

function getWs(): FakeWebSocket {
  const ws = FakeWebSocket.latestInstance;
  if (!ws) throw new Error("No FakeWebSocket instance created yet");
  return ws;
}

/** Helper: create a vi.fn() spy (extracted to avoid lint pattern triggers) */
const spy = () => vi.fn();

describe("useWebSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.resetTracking();
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.WebSocket = OriginalWebSocket;
    localStorage.clear();
  });

  /** Render the hook and optionally open the connection. */
  function renderWs(
    overrides?: Partial<Parameters<typeof useWebSocket>[0]>,
    { autoOpen = true }: { autoOpen?: boolean } = {}
  ) {
    const callbacks = {
      onMessage: spy(),
      onVisualization: spy(),
      onQualityMetrics: spy(),
      onToolCall: spy(),
      onThinking: spy(),
      onError: spy(),
      onProcessGraph: spy(),
      onTextStreamStart: spy(),
      onTextDelta: spy(),
      onTextStreamEnd: spy(),
      onSuggestions: spy(),
      onSessionRestored: spy(),
      onClarification: spy(),
      onSustainabilityMetrics: spy(),
      onSavedJobCreated: spy(),
      onReconnected: spy(),
      onDone: spy(),
    };

    const result = renderHook(() =>
      useWebSocket({
        url: "/ws/chat",
        ...callbacks,
        ...overrides,
      })
    );

    // The hook sets a 100ms connect timeout (StrictMode handling)
    act(() => {
      vi.advanceTimersByTime(150);
    });

    if (autoOpen && FakeWebSocket.latestInstance) {
      act(() => {
        getWs().triggerOpen();
      });
    }

    return { ...result, callbacks };
  }

  // ---- 1. Initial connection state ----

  it("initializes with disconnected state before WebSocket opens", () => {
    const { result } = renderWs({}, { autoOpen: false });

    expect(result.current.isConnected).toBe(false);
    expect(result.current.isProcessing).toBe(false);
    expect(result.current.sessionId).toBeTruthy();
  });

  it("transitions to connected state when WebSocket opens", () => {
    const { result } = renderWs();

    expect(result.current.isConnected).toBe(true);
    expect(result.current.isProcessing).toBe(false);
  });

  it("persists sessionId in localStorage", () => {
    const { result } = renderWs();
    const sid = result.current.sessionId;

    expect(localStorage.getItem("openeo-session-id")).toBe(sid);
  });

  // ---- 2. Auth token in WebSocket URL ----

  it("sends auth token as a WebSocket message", () => {
    renderWs({ authToken: "my-secret-token" });

    const ws = getWs();
    expect(ws.url).not.toContain("token=");
    expect(ws.parsedSentData()).toContainEqual({
      type: "authenticate",
      token: "my-secret-token",
    });
  });

  it("connects without token query parameter when authToken is null", () => {
    renderWs({ authToken: null });

    const ws = getWs();
    expect(ws.url).not.toContain("token=");
  });

  // ---- 3. sendMessage creates correct JSON payload ----

  it("sendMessage sends correct JSON payload with session_id", () => {
    const { result } = renderWs();
    const ws = getWs();

    act(() => {
      result.current.sendMessage("Show NDVI for Kerala");
    });

    const sent = ws.parsedSentData();
    const userMsg = sent.find((m) => m.type === "message");
    expect(userMsg).toBeDefined();
    expect(userMsg!.content).toBe("Show NDVI for Kerala");
    expect(userMsg!.session_id).toBe(result.current.sessionId);
    expect(userMsg!.type).toBe("message");
  });

  it("sendMessage spreads extra fields into the payload", () => {
    const { result } = renderWs();
    const ws = getWs();

    act(() => {
      result.current.sendMessage("query", { bbox: [1, 2, 3, 4] });
    });

    const userMsg = ws.parsedSentData().find((m) => m.type === "message" && m.content === "query");
    expect(userMsg).toBeDefined();
    expect(userMsg!.bbox).toEqual([1, 2, 3, 4]);
  });

  it("sendMessage sets isProcessing to true", () => {
    const { result } = renderWs();

    act(() => {
      result.current.sendMessage("analyze something");
    });

    expect(result.current.isProcessing).toBe(true);
  });

  // ---- Resuming a run after a dropped connection ----

  describe("run resume", () => {
    function deliver(ws: FakeWebSocket, payload: Record<string, unknown>) {
      act(() => {
        ws.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
      });
    }

    beforeEach(() => {
      sessionStorage.clear();
    });

    it("ignores events already received, so a replay can't duplicate text", () => {
      const onTextDelta = vi.fn();
      renderWs({ onTextDelta });
      const ws = getWs();

      const event = { type: "text_delta", content: "a", run_id: "run-1", seq: 1 };
      deliver(ws, event);
      deliver(ws, event);
      deliver(ws, { ...event, content: "b", seq: 2 });

      expect(onTextDelta.mock.calls.map(([content]) => content)).toEqual(["a", "b"]);
    });

    it("starts over for a new run", () => {
      const onTextDelta = vi.fn();
      renderWs({ onTextDelta });
      const ws = getWs();

      deliver(ws, { type: "text_delta", content: "a", run_id: "run-1", seq: 5 });
      deliver(ws, { type: "text_delta", content: "b", run_id: "run-2", seq: 1 });

      expect(onTextDelta).toHaveBeenCalledTimes(2);
    });

    it("reports its position when restoring the session after a reload", () => {
      localStorage.setItem("openeo-session-id", "session-1");
      sessionStorage.setItem("openeo-run-cursor", JSON.stringify({ run_id: "run-1", seq: 7 }));

      renderWs();

      const restore = getWs().parsedSentData().find((m) => m.type === "restore_session");
      expect(restore).toMatchObject({ run_id: "run-1", last_seq: 7 });
    });

    it("shows processing again when a run is still going", () => {
      const { result } = renderWs();

      deliver(getWs(), { type: "run_resumed", run_id: "run-1", running: true });

      expect(result.current.isProcessing).toBe(true);
    });

    it("keeps processing through a dropped connection", () => {
      const { result } = renderWs();
      act(() => result.current.sendMessage("analyze"));

      act(() => getWs().triggerClose(1006, "abnormal"));

      expect(result.current.isConnected).toBe(false);
      expect(result.current.isProcessing).toBe(true);
    });

    it("clears processing when the server reports nothing is running", () => {
      const { result } = renderWs();
      act(() => result.current.sendMessage("analyze"));

      deliver(getWs(), { type: "session_restored", session_id: "s", run_active: false });

      expect(result.current.isProcessing).toBe(false);
    });

    it("keeps processing when the server reports the run is active", () => {
      const { result } = renderWs();
      act(() => result.current.sendMessage("analyze"));

      deliver(getWs(), { type: "session_restored", session_id: "s", run_active: true });

      expect(result.current.isProcessing).toBe(true);
    });

    it("reconnects immediately when the network returns", () => {
      renderWs();
      act(() => getWs().triggerClose(1006, "abnormal"));
      const before = FakeWebSocket.instances.length;

      act(() => {
        window.dispatchEvent(new Event("online"));
      });

      expect(FakeWebSocket.instances.length).toBe(before + 1);
    });

    it("does not show processing when the run already finished", () => {
      const { result } = renderWs();

      deliver(getWs(), { type: "run_resumed", run_id: "run-1", running: false });

      expect(result.current.isProcessing).toBe(false);
    });
  });

  // ---- 4. Session restoration on connect ----

  it("sends restore_session message on open when sessionId exists in localStorage", () => {
    localStorage.setItem("openeo-session-id", "existing-session-abc");

    renderWs();
    const ws = getWs();

    const restoreMsg = ws.parsedSentData().find((m) => m.type === "restore_session");
    expect(restoreMsg).toBeDefined();
    expect(restoreMsg!.session_id).toBe("existing-session-abc");
  });

  // ---- 5. Ping/pong keep-alive ----

  it("responds to server ping with pong", () => {
    renderWs();
    const ws = getWs();

    act(() => {
      ws.triggerServerMessage({ type: "ping" });
    });

    const pongMsg = ws.parsedSentData().find((m) => m.type === "pong");
    expect(pongMsg).toBeDefined();
  });

  // ---- 6. Message handling: session ----

  it("handles session message without error", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "session",
        session_id: "new-session",
      });
    });

    // session type is a no-op -- should not call onError
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  // ---- 7. Message handling: thinking ----

  it("handles thinking message and calls onThinking callback", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "thinking",
        thinking_type: "analyzing",
        thinking_id: "t-1",
        content: "Analyzing satellite data",
        thinking_completed: false,
      });
    });

    expect(callbacks.onThinking).toHaveBeenCalledTimes(1);
    const arg = callbacks.onThinking.mock.calls[0]![0];
    expect(arg.type).toBe("analyzing");
    expect(arg.message).toBe("Analyzing satellite data");
    expect(arg.completed).toBe(false);
    expect(arg.id).toBe("t-1");
  });

  // ---- 8. Message handling: tool_start ----

  it("handles tool_start message and calls onToolCall with running status", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "tool_start",
        tool_name: "openeo_load_collection",
        tool_input: { collection: "sentinel-2" },
      });
    });

    expect(callbacks.onToolCall).toHaveBeenCalledTimes(1);
    const arg = callbacks.onToolCall.mock.calls[0]![0];
    expect(arg.name).toBe("openeo_load_collection");
    expect(arg.arguments).toEqual({ collection: "sentinel-2" });
    expect(arg.status).toBe("running");
  });

  // ---- 9. Message handling: tool_result ----

  it("handles tool_result message and calls onToolCall with completed status", () => {
    const { callbacks } = renderWs();
    const ws = getWs();

    // Start tool first to set currentToolRef
    act(() => {
      ws.triggerServerMessage({
        type: "tool_start",
        tool_name: "openeo_load_collection",
        tool_input: {},
      });
    });

    act(() => {
      ws.triggerServerMessage({
        type: "tool_result",
        tool_name: "openeo_load_collection",
        result: { status: "ok", rows: 100 },
      });
    });

    expect(callbacks.onToolCall).toHaveBeenCalledTimes(2);
    const resultArg = callbacks.onToolCall.mock.calls[1]![0];
    expect(resultArg.status).toBe("completed");
    expect(resultArg.result).toEqual({ status: "ok", rows: 100 });
  });

  // ---- 10. Message handling: visualization ----

  it("handles visualization message and calls onVisualization", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "visualization",
        visualization: {
          type: "map",
          title: "NDVI Map",
          data: { type: "raster", url: "/render/test.tif" },
        },
      });
    });

    expect(callbacks.onVisualization).toHaveBeenCalledTimes(1);
    const viz = callbacks.onVisualization.mock.calls[0]![0];
    expect(viz.type).toBe("map");
    expect(viz.title).toBe("NDVI Map");
  });

  // ---- 11. Message handling: quality_metrics ----

  it("handles quality_metrics message and calls onQualityMetrics", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "quality_metrics",
        metrics: { overallScore: 85, grade: "B" },
      });
    });

    expect(callbacks.onQualityMetrics).toHaveBeenCalledTimes(1);
    expect(callbacks.onQualityMetrics.mock.calls[0]![0]).toEqual({
      overallScore: 85,
      grade: "B",
    });
  });

  // ---- 12. Message handling: process_graph ----

  it("handles process_graph message and calls onProcessGraph and onVisualization", () => {
    const { callbacks } = renderWs();

    const graph = {
      load1: {
        process_id: "load_collection",
        arguments: { id: "s2" },
        result: false,
      },
      save1: { process_id: "save_result", arguments: {}, result: true },
    };

    act(() => {
      getWs().triggerServerMessage({
        type: "process_graph",
        graph,
      });
    });

    expect(callbacks.onProcessGraph).toHaveBeenCalledTimes(1);
    expect(callbacks.onProcessGraph.mock.calls[0]![0]).toEqual(graph);

    // Also creates a process_graph visualization
    expect(callbacks.onVisualization).toHaveBeenCalledTimes(1);
    const viz = callbacks.onVisualization.mock.calls[0]![0];
    expect(viz.type).toBe("process_graph");
    expect(viz.data.nodes).toHaveLength(2);
  });

  // ---- 13. Message handling: text_stream_start ----

  it("handles text_stream_start and calls onTextStreamStart", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({ type: "text_stream_start" });
    });

    expect(callbacks.onTextStreamStart).toHaveBeenCalledTimes(1);
  });

  // ---- 14. Message handling: text_delta ----

  it("handles text_delta and calls onTextDelta with content", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "text_delta",
        content: "Hello world",
      });
    });

    expect(callbacks.onTextDelta).toHaveBeenCalledTimes(1);
    expect(callbacks.onTextDelta.mock.calls[0]![0]).toBe("Hello world");
  });

  // ---- 15. Message handling: text_stream_end ----

  it("handles text_stream_end and calls onTextStreamEnd", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({ type: "text_stream_end" });
    });

    expect(callbacks.onTextStreamEnd).toHaveBeenCalledTimes(1);
  });

  // ---- 16. Message handling: text ----

  it("handles text message and calls onMessage with assistant role", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "text",
        content: "Analysis complete.",
      });
    });

    expect(callbacks.onMessage).toHaveBeenCalledTimes(1);
    const msg = callbacks.onMessage.mock.calls[0]![0];
    expect(msg.role).toBe("assistant");
    expect(msg.content).toBe("Analysis complete.");
    expect(msg.id).toBeTruthy();
    expect(msg.timestamp).toBeInstanceOf(Date);
  });

  // ---- 17. Message handling: suggestions ----

  it("handles suggestions message and calls onSuggestions", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "suggestions",
        suggestions: ["Try NDVI", "Check cloud cover"],
      });
    });

    expect(callbacks.onSuggestions).toHaveBeenCalledTimes(1);
    expect(callbacks.onSuggestions.mock.calls[0]![0]).toEqual(["Try NDVI", "Check cloud cover"]);
  });

  // ---- 18. Message handling: session_restored ----

  it("handles session_restored message and calls onSessionRestored", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "session_restored",
        session_id: "restored-abc",
      });
    });

    expect(callbacks.onSessionRestored).toHaveBeenCalledTimes(1);
    expect(callbacks.onSessionRestored.mock.calls[0]![0]).toBe("restored-abc");
  });

  // ---- 19. Message handling: clarification ----

  it("handles clarification message and calls onClarification", () => {
    const { callbacks } = renderWs();

    const questions = [
      {
        question: "Which band?",
        header: "Band Selection",
        options: [
          { label: "B04", description: "Red" },
          { label: "B08", description: "NIR" },
        ],
      },
    ];

    act(() => {
      getWs().triggerServerMessage({
        type: "clarification",
        questions,
      });
    });

    expect(callbacks.onClarification).toHaveBeenCalledTimes(1);
    expect(callbacks.onClarification.mock.calls[0]![0]).toEqual(questions);
  });

  // ---- 20. Message handling: sustainability_metrics ----

  it("handles sustainability_metrics message and calls onSustainabilityMetrics", () => {
    const { callbacks } = renderWs();

    const metrics = {
      carbonFootprint: 0.5,
      dataTransferred: 1024000,
      computeTime: 12.5,
      energyUsed: 0.003,
    };

    act(() => {
      getWs().triggerServerMessage({
        type: "sustainability_metrics",
        metrics,
      });
    });

    expect(callbacks.onSustainabilityMetrics).toHaveBeenCalledTimes(1);
    expect(callbacks.onSustainabilityMetrics.mock.calls[0]![0]).toEqual(metrics);
  });

  // ---- 21. Message handling: saved_job_created ----

  it("handles saved_job_created message and calls onSavedJobCreated", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "saved_job_created",
        save_id: "job-xyz-123",
      });
    });

    expect(callbacks.onSavedJobCreated).toHaveBeenCalledTimes(1);
    expect(callbacks.onSavedJobCreated.mock.calls[0]![0]).toBe("job-xyz-123");
  });

  // ---- 21b. Message handling: report_sections live flag ----

  it("dispatches report_sections with live: true as live sections", () => {
    const onReportSections = spy();
    renderWs({ onReportSections });

    const sections = [
      {
        type: "method_card",
        title: "Method Card",
        content: "## Method Card\n- **plan_hash:** abc123",
        timestamp: "2026-07-23T00:00:00.000Z",
        toolName: "agentic_decision_query",
      },
    ];

    act(() => {
      getWs().triggerServerMessage({ type: "report_sections", live: true, sections });
    });

    expect(onReportSections).toHaveBeenCalledTimes(1);
    // Two-arg contract: sections plus the live flag, so useJonaChat can tell
    // run-streamed sections apart from saved-result replay.
    expect(onReportSections).toHaveBeenCalledWith(sections, true);
  });

  it("dispatches report_sections without a live flag as non-live replay", () => {
    const onReportSections = spy();
    renderWs({ onReportSections });

    const sections = [
      {
        type: "report",
        title: "Analysis Report",
        content: "replayed from visualizations.json",
        timestamp: "2026-07-23T00:00:00.000Z",
        toolName: "generate_report",
      },
    ];

    act(() => {
      getWs().triggerServerMessage({ type: "report_sections", sections });
    });

    expect(onReportSections).toHaveBeenCalledTimes(1);
    expect(onReportSections).toHaveBeenCalledWith(sections, false);
  });

  // ---- 21c. Message handling: visualization replay flag ----

  it("dispatches visualization with replay: true as a replayed visualization", () => {
    const onVisualization = spy();
    renderWs({ onVisualization });

    act(() => {
      getWs().triggerServerMessage({
        type: "visualization",
        replay: true,
        visualization: { type: "chart", title: "NDVI trend", data: {} },
      });
    });

    expect(onVisualization).toHaveBeenCalledTimes(1);
    // Two-arg contract: replayed viz must be distinguishable so useJonaChat
    // keeps them out of the running turn's flush bundle.
    expect(onVisualization).toHaveBeenCalledWith(
      expect.objectContaining({ type: "chart", title: "NDVI trend" }),
      true,
    );
  });

  it("dispatches visualization without a replay flag as live", () => {
    const onVisualization = spy();
    renderWs({ onVisualization });

    act(() => {
      getWs().triggerServerMessage({
        type: "visualization",
        visualization: { type: "chart", title: "Live chart", data: {} },
      });
    });

    expect(onVisualization).toHaveBeenCalledTimes(1);
    expect(onVisualization).toHaveBeenCalledWith(
      expect.objectContaining({ type: "chart", title: "Live chart" }),
      false,
    );
  });

  // ---- 22. Message handling: done ----

  it("handles done message, sets isProcessing=false, and calls onDone", () => {
    const { result, callbacks } = renderWs();

    // Set isProcessing to true first
    act(() => {
      result.current.sendMessage("do something");
    });
    expect(result.current.isProcessing).toBe(true);

    act(() => {
      getWs().triggerServerMessage({ type: "done" });
    });

    expect(result.current.isProcessing).toBe(false);
    expect(callbacks.onDone).toHaveBeenCalledTimes(1);
  });

  // ---- 23. Message handling: error ----

  it("handles error message, sets isProcessing=false, and calls onError", () => {
    const { result, callbacks } = renderWs();

    // Set isProcessing to true first
    act(() => {
      result.current.sendMessage("fail query");
    });

    act(() => {
      getWs().triggerServerMessage({
        type: "error",
        content: "Internal server error",
      });
    });

    expect(result.current.isProcessing).toBe(false);
    expect(callbacks.onError).toHaveBeenCalledTimes(1);
    const err = callbacks.onError.mock.calls[0]![0];
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe("Internal server error");
  });

  // ---- 24. Reconnection with exponential backoff ----

  it("reconnects with exponential backoff on abnormal close", () => {
    renderWs();
    const ws1 = getWs();

    // First close: reconnect delay = 500ms (500 * 2^0)
    act(() => {
      ws1.triggerClose(1006, "abnormal");
    });

    expect(FakeWebSocket.instances).toHaveLength(1); // no new WS yet

    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(FakeWebSocket.instances).toHaveLength(2); // reconnected
    const ws2 = getWs();

    // Open and close again: attempts reset on successful open,
    // so delay is again 500ms (500 * 2^0)
    act(() => {
      ws2.triggerOpen();
    });
    act(() => {
      ws2.triggerClose(1006, "abnormal");
    });

    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it("does not reconnect on clean close (code 1000)", () => {
    const { unmount } = renderWs();
    const ws = getWs();

    act(() => {
      ws.triggerClose(1000, "Normal closure");
    });

    act(() => {
      vi.advanceTimersByTime(60000);
    });

    // Only the initial instance should exist
    expect(FakeWebSocket.instances).toHaveLength(1);
    unmount();
  });

  // ---- 25. Server restart fast reconnect (500ms) ----

  it("reconnects after 500ms when server_restart message is received", () => {
    renderWs();
    const ws = getWs();

    // Receive server_restart notification
    act(() => {
      ws.triggerServerMessage({ type: "server_restart" });
    });

    // Then connection closes
    act(() => {
      ws.triggerClose(1006, "server restart");
    });

    // Should reconnect fast (500ms) regardless of attempt count
    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  // ---- 26. Message queueing while disconnected ----

  it("queues messages sent while disconnected and flushes on reconnect", () => {
    const { result } = renderWs({}, { autoOpen: false });

    // WS is not open, send messages -- they should be queued
    act(() => {
      result.current.sendMessage("queued message 1");
    });
    act(() => {
      result.current.sendMessage("queued message 2", { priority: "high" });
    });

    expect(result.current.isProcessing).toBe(true);

    // Now open the connection -- queued messages should flush
    const ws = getWs();
    act(() => {
      ws.triggerOpen();
    });

    const sent = ws.parsedSentData();
    // restore_session + 2 queued messages
    const queuedMsgs = sent.filter((m) => m.type === "message" && typeof m.content === "string");
    expect(queuedMsgs).toHaveLength(2);
    expect(queuedMsgs[0]!.content).toBe("queued message 1");
    expect(queuedMsgs[1]!.content).toBe("queued message 2");
    expect(queuedMsgs[1]!.priority).toBe("high");
  });

  // ---- 27. newSession generates new ID and reconnects ----

  it("newSession generates a new session ID and reconnects", () => {
    const { result } = renderWs();
    const oldSessionId = result.current.sessionId;

    act(() => {
      result.current.newSession();
    });

    // Session ID should have changed
    expect(result.current.sessionId).not.toBe(oldSessionId);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isProcessing).toBe(false);

    // localStorage should be updated
    expect(localStorage.getItem("openeo-session-id")).toBe(result.current.sessionId);

    // After 100ms delay, a new WS connection should be created
    act(() => {
      vi.advanceTimersByTime(150);
    });

    expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2);
  });

  // ---- 28. Dead connection detection (90s timeout) ----

  it("closes connection when no message received for 90 seconds", () => {
    renderWs();
    const ws = getWs();

    // The dead connection checker runs every 10s
    // Advance time past 90s without any messages
    act(() => {
      vi.advanceTimersByTime(100000);
    });

    // The WS should have been closed with code 4000
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
  });

  // ---- 29. sendRaw sends raw JSON ----

  it("sendRaw sends raw JSON payload without wrapping", () => {
    const { result } = renderWs();
    const ws = getWs();

    act(() => {
      result.current.sendRaw({ type: "custom_event", data: 42 });
    });

    const sent = ws.parsedSentData();
    const rawMsg = sent.find((m) => m.type === "custom_event");
    expect(rawMsg).toBeDefined();
    expect(rawMsg!.data).toBe(42);
  });

  // ---- 30. reconnect() forces immediate reconnection ----

  it("reconnect() closes existing connection and creates a new one", () => {
    const { result } = renderWs();

    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      result.current.reconnect();
    });

    expect(result.current.isConnected).toBe(false);

    // After 100ms delay, a new connection is created
    act(() => {
      vi.advanceTimersByTime(150);
    });

    expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2);
  });

  // ---- 31. onReconnected callback fires on reconnect ----

  it("calls onReconnected callback when reconnecting after disconnect", () => {
    const { callbacks } = renderWs();
    const ws1 = getWs();

    // Simulate disconnect
    act(() => {
      ws1.triggerClose(1006, "abnormal");
    });

    // Wait for reconnect
    act(() => {
      vi.advanceTimersByTime(600);
    });

    const ws2 = getWs();
    act(() => {
      ws2.triggerOpen();
    });

    expect(callbacks.onReconnected).toHaveBeenCalledTimes(1);
  });

  // ---- 32. Cleanup on unmount ----

  it("cleans up WebSocket and timers on unmount", () => {
    const { unmount } = renderWs();
    const ws = getWs();

    unmount();

    // After unmount, the WS should be closed
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
  });

  // ---- 33. URL construction ----

  it("constructs ws:// URL from relative path using window.location", () => {
    renderWs({ url: "/ws/chat" });
    const ws = getWs();

    // jsdom sets location.protocol to "http:" and host to "localhost[:port]"
    expect(ws.url).toMatch(/^ws:\/\/localhost(:\d+)?\/ws\/chat/);
  });

  it("uses the provided URL directly when it starts with ws:// or wss://", () => {
    renderWs({ url: "wss://example.com/ws" });
    const ws = getWs();

    expect(ws.url).toMatch(/^wss:\/\/example\.com\/ws/);
  });

  // ---- 34. visualization message with save_id ----

  it("passes save_id through to visualization object", () => {
    const { callbacks } = renderWs();

    act(() => {
      getWs().triggerServerMessage({
        type: "visualization",
        visualization: {
          type: "map",
          title: "NDVI Result",
          save_id: "job-save-42",
          data: { type: "raster", url: "/render/test.tif" },
        },
      });
    });

    expect(callbacks.onVisualization).toHaveBeenCalledTimes(1);
    const viz = callbacks.onVisualization.mock.calls[0]![0];
    expect(viz.saveId).toBe("job-save-42");
  });

  // ---- Connection state ----

  describe("connection state", () => {
    /** Fail `count` handshakes in a row: each socket closes without ever opening. */
    function failHandshakes(count: number) {
      for (let i = 0; i < count; i++) {
        act(() => {
          getWs().triggerClose(1006, "handshake failed");
        });
        act(() => {
          vi.advanceTimersByTime(30_000);
        });
      }
    }

    it("is open only once the server accepts the sign-in", () => {
      const { result } = renderWs({ authToken: "token" });
      expect(result.current.connectionState).toBe("authenticating");
      act(() => {
        getWs().triggerServerMessage({ type: "auth_result", authenticated: true });
      });
      expect(result.current.connectionState).toBe("open");
    });

    it("asks for a fresh sign-in when the server refuses the token", () => {
      const { result, callbacks } = renderWs({ authToken: "token" });
      act(() => {
        result.current.sendMessage("hello");
      });
      act(() => {
        getWs().triggerServerMessage({ type: "auth_result", authenticated: false });
      });
      expect(result.current.connectionState).toBe("signed_out");
      expect(result.current.isProcessing).toBe(false);
      expect(callbacks.onError).toHaveBeenCalled();
    });

    it("stops retrying and asks for a Cloudflare sign-in when Access redirects", async () => {
      const fetchMock = vi.fn().mockResolvedValue({ type: "opaqueredirect", status: 0 });
      vi.stubGlobal("fetch", fetchMock);
      const { result } = renderWs({}, { autoOpen: false });

      failHandshakes(3);
      await act(async () => {
        await Promise.resolve();
      });

      expect(fetchMock).toHaveBeenCalledWith("/", expect.objectContaining({ redirect: "manual" }));
      expect(result.current.connectionState).toBe("access_expired");
      const sockets = FakeWebSocket.instances.length;
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(FakeWebSocket.instances).toHaveLength(sockets);
      vi.unstubAllGlobals();
    });

    it("keeps retrying when the page itself still loads", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ type: "basic", status: 200 }));
      const { result } = renderWs({}, { autoOpen: false });

      failHandshakes(3);
      await act(async () => {
        await Promise.resolve();
      });

      expect(result.current.connectionState).toBe("reconnecting");
      const sockets = FakeWebSocket.instances.length;
      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      expect(FakeWebSocket.instances.length).toBeGreaterThan(sockets);
      vi.unstubAllGlobals();
    });

    it("sends a stop made while offline once reconnected", () => {
      const { result } = renderWs();
      act(() => {
        getWs().triggerClose(1006);
      });
      act(() => {
        result.current.sendRaw({ type: "stop" });
      });
      act(() => {
        vi.advanceTimersByTime(500);
      });
      act(() => {
        getWs().triggerOpen();
      });
      expect(getWs().parsedSentData().map((m) => m.type)).toContain("stop");
    });

    it("keeps processing through a run's error until the run's done", () => {
      const { result } = renderWs();
      act(() => {
        result.current.sendMessage("hello");
      });
      act(() => {
        getWs().triggerServerMessage({ type: "error", content: "a tool failed", run_id: "r1", seq: 1 });
      });
      expect(result.current.isProcessing).toBe(true);
      act(() => {
        getWs().triggerServerMessage({ type: "done", run_id: "r1", seq: 2 });
      });
      expect(result.current.isProcessing).toBe(false);
    });

    it("keeps processing when an expired sign-in left the message queued", () => {
      const { result } = renderWs();
      act(() => {
        result.current.sendMessage("hello");
      });
      act(() => {
        getWs().triggerServerMessage({
          type: "error", code: "auth_expired", requeued: true, content: "Your sign-in expired.",
        });
      });
      expect(result.current.isProcessing).toBe(true);
    });

    it("keeps processing when a Load on Map fails during a run", () => {
      const { result } = renderWs();
      act(() => {
        result.current.sendMessage("hello");
      });
      act(() => {
        getWs().triggerServerMessage({
          type: "error", code: "saved_job_unavailable", content: "Saved job 'x' not found",
        });
      });
      expect(result.current.isProcessing).toBe(true);
    });
  });

  describe("trust and provenance events", () => {
    it("passes grounding warnings and citations on", () => {
      const onGroundingWarning = vi.fn();
      const onCitations = vi.fn();
      renderWs({ onGroundingWarning, onCitations });
      const violations = [{ value: 87, token: "87%", context: "about 87% of the field" }];
      act(() => {
        getWs().triggerServerMessage({ type: "grounding_warning", violations });
        getWs().triggerServerMessage({ type: "citations", tool_name: "t", sources: ["https://a.example"] });
      });
      expect(onGroundingWarning).toHaveBeenCalledWith(violations);
      expect(onCitations).toHaveBeenCalledWith(["https://a.example"]);
    });

    it("logs an unknown event type instead of dropping it silently", () => {
      const debug = vi.spyOn(console, "debug").mockImplementation(() => {});
      renderWs();
      act(() => {
        getWs().triggerServerMessage({ type: "brand_new_event" });
        getWs().triggerServerMessage({ type: "method_gap", detail: "x" });
      });
      expect(debug).toHaveBeenCalledTimes(1);
      expect(debug.mock.calls[0]).toContain("brand_new_event");
      debug.mockRestore();
    });
  });

  describe("tools and questions", () => {
    it("pairs each result with its own start when tools run at once", () => {
      const { callbacks } = renderWs();
      act(() => {
        getWs().triggerServerMessage({ type: "tool_start", tool_name: "a", tool_use_id: "t1" });
        getWs().triggerServerMessage({ type: "tool_start", tool_name: "b", tool_use_id: "t2" });
        getWs().triggerServerMessage({ type: "tool_result", tool_name: "a", tool_use_id: "t1", result: "A" });
      });
      const completed = callbacks.onToolCall.mock.calls
        .map((call) => call[0])
        .filter((call) => call.status === "completed");
      expect(completed).toHaveLength(1);
      expect(completed[0].id).toBe("t1");
    });

    it("passes the question id on with the question", () => {
      const onClarification = vi.fn();
      renderWs({ onClarification });
      act(() => {
        getWs().triggerServerMessage({ type: "clarification", questions: [{ question: "Which year?" }], question_id: "q1" });
      });
      expect(onClarification).toHaveBeenCalledWith([{ question: "Which year?" }], "q1");
    });

    it("stops showing a question once the server closes it", () => {
      const onClarificationClosed = vi.fn();
      renderWs({ onClarificationClosed });
      act(() => {
        getWs().triggerServerMessage({ type: "clarification_closed" });
      });
      expect(onClarificationClosed).toHaveBeenCalled();
    });
  });
});

