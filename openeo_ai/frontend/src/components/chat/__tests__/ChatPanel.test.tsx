/**
 * ChatPanel component tests.
 *
 * Validates empty state rendering, message list display, thinking indicator,
 * connection status banner, suggestion chips, clarification card, auto-scroll
 * behaviour, and onSendMessage callback propagation.
 *
 * Heavy child components (ChatMessage, AnalysisTemplates, ChatInput,
 * ThinkingIndicator, ClarificationCard, SuggestionChips) are replaced
 * with lightweight stand-ins so tests focus on ChatPanel's own orchestration
 * logic rather than child rendering internals. This follows the same pattern
 * used in SavedJobsPanel.test.tsx and spec-helpers.tsx standInComponent().
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatPanel } from "../ChatPanel";
import { createSpy, resetSpies } from "@/lib/spec-helpers";
import type { Message, ThinkingStep, ClarificationQuestion } from "@/types";

// ---------------------------------------------------------------------------
// Stand-in child components (lightweight replacements for heavy deps)
// ---------------------------------------------------------------------------

// Track the latest props passed to ChatInput so we can invoke callbacks
// @ts-expect-error -- variable used to capture props for potential future assertions
let _chatInputProps: Record<string, unknown> = {};

vi.mock("../ChatMessage", () => ({
  ChatMessage: ({ message }: { message: { id: string; content?: string } }) => (
    <div data-testid={`chat-message-${message.id}`}>
      {message.content ?? "msg"}
    </div>
  ),
}));

vi.mock("../ChatInput", () => ({
  ChatInput: (props: Record<string, unknown>) => {
    _chatInputProps = props;
    return (
      <div data-testid="chat-input">
        <button
          data-testid="send-button"
          onClick={() => (props.onSend as (m: string) => void)("test message")}
        >
          Send
        </button>
        <span data-testid="input-placeholder">
          {props.placeholder as string}
        </span>
        <span data-testid="input-disabled">{String(props.disabled)}</span>
      </div>
    );
  },
}));

vi.mock("../ThinkingIndicator", () => ({
  ThinkingIndicator: ({
    steps,
    isProcessing,
  }: {
    steps: ThinkingStep[];
    isProcessing: boolean;
  }) => (
    <div data-testid="thinking-indicator">
      {isProcessing ? "thinking" : "idle"} steps={steps.length}
    </div>
  ),
}));

vi.mock("../SuggestionChips", () => ({
  SuggestionChips: ({
    suggestions,
    onSelect,
  }: {
    suggestions: string[];
    onSelect: (s: string) => void;
  }) => (
    <div data-testid="suggestion-chips">
      {suggestions.map((s, i) => (
        <button
          key={i}
          data-testid={`suggestion-${i}`}
          onClick={() => onSelect(s)}
        >
          {s}
        </button>
      ))}
    </div>
  ),
}));

vi.mock("../ClarificationCard", () => ({
  ClarificationCard: ({
    questions,
    onSubmit,
  }: {
    questions: ClarificationQuestion[];
    onSubmit: (a: Record<string, string | string[]>) => void;
  }) => (
    <div data-testid="clarification-card">
      {questions.map((q, i) => (
        <span key={i}>{q.question}</span>
      ))}
      <button
        data-testid="clarification-submit"
        onClick={() => onSubmit({ "0": "Option A" })}
      >
        Submit
      </button>
    </div>
  ),
}));

vi.mock("../AnalysisTemplates", () => ({
  AnalysisTemplates: ({
    onSelectTemplate,
  }: {
    onSelectTemplate: (p: string) => void;
  }) => (
    <div data-testid="analysis-templates">
      <button
        data-testid="template-btn"
        onClick={() => onSelectTemplate("NDVI template prompt")}
      >
        NDVI
      </button>
    </div>
  ),
}));

// Stand-in for @tanstack/react-virtual to avoid layout-engine dependency.
// Renders items directly without virtualisation in tests.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 120,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, i) => ({
        index: i,
        key: `virt-${i}`,
        start: i * 120,
        size: 120,
      })),
    measureElement: () => {},
  }),
}));

// ---------------------------------------------------------------------------
// Test data factories
// ---------------------------------------------------------------------------

function makeMessage(overrides: Partial<Message> & { id: string }): Message {
  return {
    role: "user",
    content: `Message ${overrides.id}`,
    timestamp: new Date(),
    ...overrides,
  };
}

function makeThinkingStep(overrides: Partial<ThinkingStep> = {}): ThinkingStep {
  return {
    id: "ts-1",
    type: "analyzing",
    message: "Analyzing request...",
    timestamp: new Date(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChatPanel", () => {
  let onSendMessage: ReturnType<typeof createSpy>;

  beforeEach(() => {
    resetSpies();
    _chatInputProps = {};
    onSendMessage = createSpy();
  });

  // ---- Empty state ----

  it("renders empty state with logo and analysis templates when no messages", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    // Logo image present
    const logo = screen.getByAltText("Jona AI");
    expect(logo).toBeInTheDocument();

    // Title and subtitle
    expect(screen.getByText("Jona AI Assistant")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Analyze Earth observation data using natural language.",
      ),
    ).toBeInTheDocument();

    // Analysis templates rendered
    expect(screen.getByTestId("analysis-templates")).toBeInTheDocument();
  });

  // ---- Message rendering ----

  it("renders messages list via virtualizer when messages are provided", () => {
    const messages = [
      makeMessage({ id: "m1", role: "user", content: "Hello" }),
      makeMessage({ id: "m2", role: "assistant", content: "Hi there" }),
    ];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    // Both messages rendered through virtualizer
    expect(screen.getByTestId("chat-message-m1")).toBeInTheDocument();
    expect(screen.getByTestId("chat-message-m2")).toBeInTheDocument();

    // Empty state should NOT be shown
    expect(screen.queryByText("Jona AI Assistant")).not.toBeInTheDocument();
  });

  // ---- Thinking indicator ----

  it("shows thinking indicator when isProcessing is true", () => {
    const messages = [makeMessage({ id: "m1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={true}
        isConnected={true}
      />,
    );

    const indicator = screen.getByTestId("thinking-indicator");
    expect(indicator).toBeInTheDocument();
    expect(indicator.textContent).toContain("thinking");
  });

  it("shows thinking indicator when thinkingSteps are provided", () => {
    const messages = [makeMessage({ id: "m1" })];
    const steps = [makeThinkingStep({ id: "step-1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        thinkingSteps={steps}
      />,
    );

    const indicator = screen.getByTestId("thinking-indicator");
    expect(indicator).toBeInTheDocument();
    expect(indicator.textContent).toContain("steps=1");
  });

  it("hides thinking indicator when not processing and no steps", () => {
    const messages = [makeMessage({ id: "m1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        thinkingSteps={[]}
      />,
    );

    expect(screen.queryByTestId("thinking-indicator")).not.toBeInTheDocument();
  });

  // ---- Connection status ----

  it("shows disconnected banner when isConnected is false", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={false}
      />,
    );

    // Nothing is running, so the banner doesn't claim an analysis continues.
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
  });

  it("says a running analysis continues while reconnecting", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={true}
        isConnected={false}
        connectionState="reconnecting"
      />,
    );

    expect(screen.getByText("Reconnecting. Your analysis keeps running.")).toBeInTheDocument();
  });

  it("offers a fresh Cloudflare sign-in when Access has expired", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={false}
        connectionState="access_expired"
        onReconnect={vi.fn()}
      />,
    );

    expect(screen.getByText(/Cloudflare sign-in expired/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign in again/ })).toBeInTheDocument();
    // Reconnecting can't help here, so it isn't offered.
    expect(screen.queryByRole("button", { name: /Reconnect/ })).not.toBeInTheDocument();
  });

  it("sends the user to sign in when the server refused the sign-in", () => {
    const onSignIn = vi.fn();
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        connectionState="signed_out"
        onSignIn={onSignIn}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(onSignIn).toHaveBeenCalled();
  });

  it("hides disconnected banner when isConnected is true", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    expect(screen.queryByText("Reconnecting. Your analysis keeps running.")).not.toBeInTheDocument();
  });

  it("shows Reconnect button when onReconnect is provided and disconnected", () => {
    const onReconnect = createSpy();

    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={false}
        onReconnect={onReconnect}
      />,
    );

    const reconnectBtn = screen.getByText("Reconnect");
    expect(reconnectBtn).toBeInTheDocument();

    fireEvent.click(reconnectBtn);
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });

  it("keeps ChatInput usable and explains queueing when disconnected", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={false}
      />,
    );

    expect(screen.getByTestId("input-disabled").textContent).not.toBe("true");
    expect(screen.getByTestId("input-placeholder").textContent).toBe(
      "Offline. Your message will send when reconnected.",
    );
  });

  // ---- Suggestion chips ----

  it("renders suggestion chips when contextSuggestions provided and not processing", () => {
    const messages = [makeMessage({ id: "m1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        contextSuggestions={["Show NDVI", "Check clouds"]}
      />,
    );

    expect(screen.getByTestId("suggestion-chips")).toBeInTheDocument();
    expect(screen.getByText("Show NDVI")).toBeInTheDocument();
    expect(screen.getByText("Check clouds")).toBeInTheDocument();
  });

  it("hides suggestion chips when isProcessing is true", () => {
    const messages = [makeMessage({ id: "m1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={true}
        isConnected={true}
        contextSuggestions={["Show NDVI"]}
      />,
    );

    expect(screen.queryByTestId("suggestion-chips")).not.toBeInTheDocument();
  });

  it("fires onSendMessage when a suggestion chip is clicked", () => {
    const messages = [makeMessage({ id: "m1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        contextSuggestions={["Show NDVI"]}
      />,
    );

    fireEvent.click(screen.getByText("Show NDVI"));
    expect(onSendMessage).toHaveBeenCalledWith("Show NDVI");
  });

  // ---- Clarification card ----

  it("renders clarification card when pendingClarification is set", () => {
    const messages = [makeMessage({ id: "m1" })];
    const questions: ClarificationQuestion[] = [
      {
        question: "Which time period?",
        header: "Time Period",
        options: [
          { label: "Last month", description: "30 days" },
          { label: "Last year", description: "365 days" },
        ],
      },
    ];
    const onClarificationSubmit = createSpy();

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        pendingClarification={questions}
        onClarificationSubmit={onClarificationSubmit}
      />,
    );

    expect(screen.getByTestId("clarification-card")).toBeInTheDocument();
    expect(screen.getByText("Which time period?")).toBeInTheDocument();
  });

  it("does not render clarification card when pendingClarification is null", () => {
    const messages = [makeMessage({ id: "m1" })];

    render(
      <ChatPanel
        messages={messages}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
        pendingClarification={null}
      />,
    );

    expect(screen.queryByTestId("clarification-card")).not.toBeInTheDocument();
  });

  // ---- onSendMessage callback ----

  it("fires onSendMessage when ChatInput send button is clicked", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    fireEvent.click(screen.getByTestId("send-button"));
    expect(onSendMessage).toHaveBeenCalledWith("test message");
  });

  it("fires onSendMessage when an analysis template is selected", () => {
    render(
      <ChatPanel
        messages={[]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    fireEvent.click(screen.getByTestId("template-btn"));
    expect(onSendMessage).toHaveBeenCalledWith("NDVI template prompt");
  });

  // ---- Auto-scroll ----

  it("calls requestAnimationFrame for auto-scroll when messages change", () => {
    const rafSpy = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((cb) => {
        cb(0);
        return 0;
      });

    const { rerender } = render(
      <ChatPanel
        messages={[makeMessage({ id: "m1" })]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    rafSpy.mockClear();

    // Add a new message to trigger the auto-scroll effect
    rerender(
      <ChatPanel
        messages={[makeMessage({ id: "m1" }), makeMessage({ id: "m2" })]}
        onSendMessage={onSendMessage}
        isProcessing={false}
        isConnected={true}
      />,
    );

    expect(rafSpy).toHaveBeenCalled();
    rafSpy.mockRestore();
  });
});
