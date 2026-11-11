/**
 * AI SDK v5-inspired type definitions for JonaAI.
 *
 * These types bring the discriminated-union "parts" pattern from Vercel AI SDK v5
 * to our existing codebase, improving type safety without any runtime changes.
 *
 * Migration path:
 *   Phase 1 (current): Type-only, no runtime change. Import alongside existing types.
 *   Phase 2 (future):  Swap useWebSocket for useChat with custom WebSocketChatTransport.
 *   Phase 3 (future):  Render messages via parts array instead of flat fields.
 *
 * @see https://ai-sdk.dev/docs/ai-sdk-ui
 */

import type {
  Visualization,
  ThinkingStep,
  QualityMetrics,
  SustainabilityMetrics,
  ClarificationQuestion,
  BBox,
} from "./index";

// ---------------------------------------------------------------------------
// 1. Message Roles
// ---------------------------------------------------------------------------

/** Discriminated message roles matching AI SDK v5 conventions. */
export type MessageRole = "user" | "assistant" | "system" | "tool";

// ---------------------------------------------------------------------------
// 2. Tool Call State Machine
// ---------------------------------------------------------------------------

/**
 * Strongly-typed tool call lifecycle states.
 *
 * AI SDK v5 uses: partial-call | call | result
 * We extend with 'running' for our WebSocket streaming UX.
 */
export type ToolCallState =
  | "partial-call" // Streaming tool input (future: input_delta)
  | "call" // Tool invoked, waiting for result
  | "running" // Executing server-side (our custom state)
  | "result" // Result available
  | "error"; // Execution failed

/** Strongly-typed tool call with state discriminant. */
export type TypedToolCall =
  | {
      state: "partial-call";
      id: string;
      name: string;
      partialInput: Record<string, unknown>;
    }
  | { state: "call"; id: string; name: string; input: Record<string, unknown> }
  | {
      state: "running";
      id: string;
      name: string;
      input: Record<string, unknown>;
    }
  | {
      state: "result";
      id: string;
      name: string;
      input: Record<string, unknown>;
      output: unknown;
    }
  | {
      state: "error";
      id: string;
      name: string;
      input: Record<string, unknown>;
      errorText: string;
    };

// ---------------------------------------------------------------------------
// 3. Message Parts (discriminated union)
// ---------------------------------------------------------------------------

/**
 * A message "part" — the atomic rendering unit in AI SDK v5.
 *
 * Instead of a flat Message with optional fields, each message contains
 * an array of typed parts that can be rendered individually.
 */
export type MessagePart =
  // --- Core parts (AI SDK v5 standard) ---
  | TextPart
  | ToolCallPart
  | ToolResultPart
  // --- Custom data parts (JonaAI extensions) ---
  | VisualizationPart
  | ThinkingPart
  | QualityMetricsPart
  | SustainabilityPart
  | SuggestionsPart
  | ClarificationPart
  | ProcessGraphPart;

/** Plain text content. */
export interface TextPart {
  type: "text";
  /** The text content (may contain markdown). */
  text: string;
}

/** A tool invocation (input + optional output). */
export interface ToolCallPart {
  type: "tool-call";
  /** Unique ID for this tool invocation. */
  toolCallId: string;
  /** Tool name (e.g. "openeo_load_collection"). */
  toolName: string;
  /** Input arguments passed to the tool. */
  input: Record<string, unknown>;
  /** Current lifecycle state. */
  state: ToolCallState;
}

/** The result of a tool invocation. */
export interface ToolResultPart {
  type: "tool-result";
  /** Matches the toolCallId of the corresponding ToolCallPart. */
  toolCallId: string;
  /** Tool name. */
  toolName: string;
  /** The result payload. */
  output: unknown;
}

/** A visualization (map, chart, table, process graph). */
export interface VisualizationPart {
  type: "data-visualization";
  id: string;
  data: Visualization;
}

/** An AI thinking/processing step. */
export interface ThinkingPart {
  type: "data-thinking";
  id: string;
  data: ThinkingStep;
}

/** Quality metrics for the current result. */
export interface QualityMetricsPart {
  type: "data-quality-metrics";
  id: string;
  data: QualityMetrics;
}

/** Sustainability/carbon metrics. */
export interface SustainabilityPart {
  type: "data-sustainability";
  id: string;
  data: SustainabilityMetrics;
}

/** Contextual follow-up suggestions. */
export interface SuggestionsPart {
  type: "data-suggestions";
  id: string;
  data: string[];
}

/** Clarification questions for the user. */
export interface ClarificationPart {
  type: "data-clarification";
  id: string;
  data: ClarificationQuestion[];
}

/** OpenEO process graph. */
export interface ProcessGraphPart {
  type: "data-process-graph";
  id: string;
  data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// 4. UIMessage (AI SDK v5-style)
// ---------------------------------------------------------------------------

/**
 * AI SDK v5-style UIMessage with typed parts array.
 *
 * This is the target type for Phase 2 migration. Each message contains
 * a `parts` array of discriminated-union parts instead of flat optional fields.
 */
export interface UIMessage {
  /** Unique message ID. */
  id: string;
  /** Message role. */
  role: MessageRole;
  /** Flat text content (for backward compatibility; derived from TextParts). */
  content: string;
  /** Typed parts array — the canonical representation. */
  parts: MessagePart[];
  /** Creation timestamp. */
  createdAt: Date;
  /** Message-level metadata. */
  metadata?: MessageMetadata;
}

/** Per-message metadata. */
export interface MessageMetadata {
  /** WebSocket session ID. */
  sessionId?: string;
  /** Bounding box context attached to user messages. */
  bbox?: BBox;
  /** Active project context. */
  projectId?: string;
  /** Whether text is still streaming. */
  isStreaming?: boolean;
  /**
   * Langfuse trace ID for this assistant response (Phase D of observability
   * plan). Populated by the backend via a `trace` WebSocket event after the
   * query completes. Used to attach user feedback scores to the correct trace.
   */
  traceId?: string;
  /** Numbers in this answer that no tool result supports. */
  groundingWarnings?: import("./index").GroundingViolation[];
  /** Source URLs the turn's tools reported. */
  sources?: string[];
}

// ---------------------------------------------------------------------------
// 5. Backend Message Protocol (typed discriminated union)
// ---------------------------------------------------------------------------

/**
 * Strongly-typed backend WebSocket message.
 *
 * Replaces the loosely-typed `BackendMessage` interface in useWebSocket.ts.
 * Each variant carries only the fields relevant to its type.
 */
export type BackendWsMessage =
  | { type: "session"; session_id: string }
  | { type: "session_restored"; session_id: string }
  | { type: "text"; content: string }
  | { type: "text_stream_start" }
  | { type: "text_delta"; content: string }
  | { type: "text_stream_end" }
  | {
      type: "tool_start";
      tool_name: string;
      tool_input?: Record<string, unknown>;
    }
  | { type: "tool_result"; tool_name?: string; result: unknown }
  | { type: "visualization"; visualization: Visualization }
  | { type: "quality_metrics"; metrics: QualityMetrics }
  | { type: "sustainability_metrics"; metrics: SustainabilityMetrics }
  | { type: "process_graph"; graph: Record<string, unknown> }
  | {
      type: "thinking";
      content?: string;
      thinking_type?: ThinkingStep["type"];
      thinking_id?: string;
      thinking_completed?: boolean;
    }
  | { type: "suggestions"; suggestions: string[] }
  | { type: "clarification"; questions: ClarificationQuestion[] }
  | { type: "done" }
  | { type: "error"; content: string }
  | { type: "server_restart" }
  | { type: "trace"; trace_id: string };

// ---------------------------------------------------------------------------
// 6. Type Guards
// ---------------------------------------------------------------------------

/** Check if a message part is a text part. */
export function isTextPart(part: MessagePart): part is TextPart {
  return part.type === "text";
}

/** Check if a message part is a tool call part. */
export function isToolCallPart(part: MessagePart): part is ToolCallPart {
  return part.type === "tool-call";
}

/** Check if a message part is a tool result part. */
export function isToolResultPart(part: MessagePart): part is ToolResultPart {
  return part.type === "tool-result";
}

/** Check if a message part is a visualization part. */
export function isVisualizationPart(part: MessagePart): part is VisualizationPart {
  return part.type === "data-visualization";
}

/** Check if a message part is a thinking part. */
export function isThinkingPart(part: MessagePart): part is ThinkingPart {
  return part.type === "data-thinking";
}

/** Check if a message part is a suggestions part. */
export function isSuggestionsPart(part: MessagePart): part is SuggestionsPart {
  return part.type === "data-suggestions";
}

/** Check if a message part is a clarification part. */
export function isClarificationPart(part: MessagePart): part is ClarificationPart {
  return part.type === "data-clarification";
}

/** Check if a message part is a process graph part. */
export function isProcessGraphPart(part: MessagePart): part is ProcessGraphPart {
  return part.type === "data-process-graph";
}

/** Check if a message part is any custom data part. */
export function isDataPart(
  part: MessagePart
): part is Exclude<MessagePart, TextPart | ToolCallPart | ToolResultPart> {
  return part.type.startsWith("data-");
}

// ---------------------------------------------------------------------------
// 7. Conversion Utilities (for Phase 2 migration)
// ---------------------------------------------------------------------------

/**
 * Extract all parts of a specific type from a UIMessage.
 *
 * @example
 *   const visualizations = extractParts(message, 'data-visualization')
 *   // TypeScript knows: visualizations is VisualizationPart[]
 */
export function extractParts<T extends MessagePart["type"]>(
  message: UIMessage,
  type: T
): Extract<MessagePart, { type: T }>[] {
  return message.parts.filter((p): p is Extract<MessagePart, { type: T }> => p.type === type);
}

/**
 * Extract all parts of a specific type from an array of UIMessages.
 *
 * @example
 *   const allVisualizations = extractAllParts(messages, 'data-visualization')
 */
export function extractAllParts<T extends MessagePart["type"]>(
  messages: UIMessage[],
  type: T
): Extract<MessagePart, { type: T }>[] {
  return messages.flatMap((m) => extractParts(m, type));
}

/**
 * Get the combined text content from a UIMessage's text parts.
 */
export function getTextContent(message: UIMessage): string {
  return message.parts
    .filter(isTextPart)
    .map((p) => p.text)
    .join("");
}

/**
 * Check if any message in the array has an active (non-completed) thinking part.
 */
export function hasActiveThinking(messages: UIMessage[]): boolean {
  const lastMessage = messages[messages.length - 1];
  if (!lastMessage) return false;
  return lastMessage.parts.some((p) => isThinkingPart(p) && !p.data.completed);
}
