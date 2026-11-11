/**
 * WebSocket event simulator for useJonaChat tests.
 *
 * Provides a controllable replacement for the useWebSocket transport layer,
 * allowing useJonaChat unit tests to trigger real callback flows
 * (text streaming, tool calls, errors) without a live WebSocket connection.
 *
 * This is standard test isolation — separating the state management layer
 * (useJonaChat) from the transport layer (useWebSocket) for focused testing.
 */
import { vi } from "vitest";
import type { ToolCall, ClarificationQuestion } from "@/types";

export interface WsSimulatorControls {
  /** All callbacks passed by useJonaChat into useWebSocket. */
  callbacks: Record<string, ((...args: unknown[]) => void) | undefined>;

  /** Captured sendMessage calls: [content, extra?] */
  sentMessages: [string, Record<string, unknown> | undefined][];

  /** Captured sendRaw calls */
  sentRaw: Record<string, unknown>[];

  /** Simulate text streaming: start -> deltas -> end */
  simulateTextStream: (chunks: string[]) => void;

  /** Simulate a tool call lifecycle (running -> completed) */
  simulateToolCall: (name: string, args: Record<string, unknown>, result: unknown) => void;

  /** Simulate an error event */
  simulateError: (message: string) => void;

  /** Simulate done event */
  simulateDone: () => void;

  /** Simulate suggestions */
  simulateSuggestions: (suggestions: string[]) => void;

  /** Simulate clarification questions */
  simulateClarification: (questions: ClarificationQuestion[]) => void;

  /** Reset all captured state between tests */
  reset: () => void;
}

let _controls: WsSimulatorControls;

/** Get the simulator controls for the current test. */
export function getWsSimulator(): WsSimulatorControls {
  return _controls;
}

/**
 * Creates a useWebSocket replacement for testing.
 * Captures callbacks from useJonaChat and exposes controllable send functions.
 */
export function createWsSimulator() {
  const controls: WsSimulatorControls = {
    callbacks: {},
    sentMessages: [],
    sentRaw: [],

    simulateTextStream(chunks: string[]) {
      controls.callbacks.onTextStreamStart?.();
      for (const chunk of chunks) {
        controls.callbacks.onTextDelta?.(chunk);
      }
      controls.callbacks.onTextStreamEnd?.();
    },

    simulateToolCall(name: string, args: Record<string, unknown>, result: unknown) {
      const toolCall: ToolCall = {
        id: `tool-${Date.now()}`,
        name,
        arguments: args,
        status: "running",
      };
      controls.callbacks.onToolCall?.(toolCall);
      controls.callbacks.onToolCall?.({
        ...toolCall,
        status: "completed",
        result,
      });
    },

    simulateError(message: string) {
      controls.callbacks.onError?.(new Error(message));
    },

    simulateDone() {
      controls.callbacks.onDone?.();
    },

    simulateSuggestions(suggestions: string[]) {
      controls.callbacks.onSuggestions?.(suggestions);
    },

    simulateClarification(questions: ClarificationQuestion[]) {
      controls.callbacks.onClarification?.(questions);
    },

    reset() {
      controls.callbacks = {};
      controls.sentMessages = [];
      controls.sentRaw = [];
    },
  };

  _controls = controls;

  return vi.fn((options: Record<string, unknown>) => {
    // Capture every callback passed from useJonaChat, so a new one can't go missing here.
    controls.callbacks = Object.fromEntries(
      Object.entries(options).filter(([name, value]) => name.startsWith("on") && typeof value === "function"),
    ) as WsSimulatorControls["callbacks"];

    return {
      isConnected: true,
      isProcessing: false,
      sendMessage: vi.fn((content: string, extra?: Record<string, unknown>) => {
        controls.sentMessages.push([content, extra]);
      }),
      sendRaw: vi.fn((payload: Record<string, unknown>) => {
        controls.sentRaw.push(payload);
      }),
      reconnect: vi.fn(),
      newSession: vi.fn(),
      sessionId: "test-session-123",
    };
  });
}
