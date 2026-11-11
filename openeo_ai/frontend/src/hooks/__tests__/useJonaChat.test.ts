/**
 * useJonaChat hook tests.
 *
 * Tests the state management layer using dependency injection.
 * A controllable transport simulator is injected via the useTransport option,
 * allowing tests to trigger real callback flows (streaming, tool calls, errors).
 */
import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { createWsSimulator, getWsSimulator } from "@/test/ws-simulator";
import type { WsSimulatorControls } from "@/test/ws-simulator";
import { useJonaChat } from "../useJonaChat";

describe("useJonaChat", () => {
  let ws: WsSimulatorControls;
  let transportHook: ReturnType<typeof createWsSimulator>;

  beforeEach(() => {
    transportHook = createWsSimulator();
  });

  function renderChat(overrides?: Record<string, unknown>) {
    const result = renderHook(() =>
      useJonaChat({
        url: "/ws",
        useTransport: transportHook as never,
        ...overrides,
      })
    );
    ws = getWsSimulator();
    return result;
  }

  // ---- 1. Initialization ----

  it("initializes with empty messages and connected state from transport", () => {
    const { result } = renderChat();

    expect(result.current.messages).toEqual([]);
    expect(result.current.uiMessages).toEqual([]);
    expect(result.current.isConnected).toBe(true);
    expect(result.current.isProcessing).toBe(false);
    expect(result.current.thinkingSteps).toEqual([]);
    expect(result.current.contextSuggestions).toEqual([]);
    expect(result.current.pendingClarification).toBeNull();
    expect(result.current.visualizations).toEqual([]);
  });

  // ---- 2. sendMessage ----

  it("sendMessage adds user message and calls transport sendMessage", () => {
    const { result } = renderChat();

    act(() => {
      result.current.sendMessage("Show NDVI for Kerala");
    });

    // User message added to state
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]!.role).toBe("user");
    expect(result.current.messages[0]!.content).toBe("Show NDVI for Kerala");

    // Transport was called
    expect(ws.sentMessages).toHaveLength(1);
    expect(ws.sentMessages[0]![0]).toBe("Show NDVI for Kerala");
  });

  // ---- 3. sendMessage with displayContent ----

  it("sendMessage with displayContent shows override in chat but sends full content", () => {
    const { result } = renderChat();

    act(() => {
      result.current.sendMessage("[Bounding box: west=76] Show NDVI", undefined, "Show NDVI");
    });

    // Message shows displayContent
    expect(result.current.messages[0]!.content).toBe("Show NDVI");

    // Transport receives full content
    expect(ws.sentMessages[0]![0]).toBe("[Bounding box: west=76] Show NDVI");
  });

  // ---- 4. Text streaming lifecycle ----

  it("handles text streaming: start -> delta x3 -> end", () => {
    const { result } = renderChat();

    act(() => {
      ws.simulateTextStream(["Hello", " world", "!"]);
    });

    // Should have one assistant message with combined content
    expect(result.current.messages).toHaveLength(1);
    const msg = result.current.messages[0]!;
    expect(msg!.role).toBe("assistant");
    expect(msg!.content).toBe("Hello world!");
    expect(msg!.isStreaming).toBe(false); // finalized after stream end
  });

  // ---- 5. Tool call creates tool-role message ----

  it("tool call completion creates tool-role message", () => {
    const { result } = renderChat();

    act(() => {
      ws.simulateToolCall(
        "openeo_load_collection",
        { collection: "sentinel-2" },
        { status: "ok", rows: 100 }
      );
    });

    // Should have a tool message with the result
    const toolMsgs = result.current.messages.filter((m) => m.role === "tool");
    expect(toolMsgs).toHaveLength(1);
    expect(toolMsgs[0]!.toolName).toBe("openeo_load_collection");
    expect(toolMsgs[0]!.toolResult).toEqual({ status: "ok", rows: 100 });
  });

  // ---- 6. Error creates system message ----

  it("error creates system message with error content", () => {
    const { result } = renderChat();

    act(() => {
      ws.simulateError("Connection timeout");
    });

    const sysMsgs = result.current.messages.filter((m) => m.role === "system");
    expect(sysMsgs).toHaveLength(1);
    expect(sysMsgs[0]!.content).toBe("Error: Connection timeout");
  });

  // ---- 7. clearChat resets all state ----

  it("clearChat resets messages, visualizations, metrics, and state", () => {
    const { result } = renderChat();

    // Add some state
    act(() => {
      result.current.sendMessage("query");
    });
    act(() => {
      ws.simulateTextStream(["response"]);
    });
    act(() => {
      ws.simulateSuggestions(["follow up 1"]);
    });

    expect(result.current.messages.length).toBeGreaterThan(0);
    expect(result.current.contextSuggestions.length).toBeGreaterThan(0);

    // Clear everything
    act(() => {
      result.current.clearChat();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.visualizations).toEqual([]);
    expect(result.current.qualityMetrics).toBeNull();
    expect(result.current.sustainabilityMetrics).toBeNull();
    expect(result.current.processGraph).toBeNull();
    expect(result.current.thinkingSteps).toEqual([]);
    expect(result.current.contextSuggestions).toEqual([]);
    expect(result.current.pendingClarification).toBeNull();
  });

  // ---- 8. uiMessages derivation ----

  it("uiMessages derives correct parts from messages", () => {
    const { result } = renderChat();

    // Send user message
    act(() => {
      result.current.sendMessage("analyze data");
    });
    // Receive assistant response
    act(() => {
      ws.simulateTextStream(["Hello from Jona"]);
    });
    // Receive tool result
    act(() => {
      ws.simulateToolCall("load_collection", {}, { data: "ok" });
    });

    const ui = result.current.uiMessages;
    expect(ui).toHaveLength(3); // user + assistant + tool

    // User message -> TextPart
    expect(ui[0]!.role).toBe("user");
    expect(ui[0]!.parts[0]).toEqual({ type: "text", text: "analyze data" });

    // Assistant message -> TextPart
    expect(ui[1]!.role).toBe("assistant");
    expect(ui[1]!.parts[0]).toEqual({ type: "text", text: "Hello from Jona" });

    // Tool message -> ToolResultPart
    expect(ui[2]!.role).toBe("tool");
    expect(ui[2]!.parts[0]!.type).toBe("tool-result");
  });

  // ---- 9. handleDone clears thinking steps ----

  it("handleDone clears thinking steps", () => {
    const { result } = renderChat();

    // Simulate thinking
    act(() => {
      ws.callbacks.onThinking?.({
        id: "t1",
        type: "analyzing",
        message: "Processing data",
        timestamp: new Date(),
      });
    });

    expect(result.current.thinkingSteps.length).toBeGreaterThan(0);

    // Done signal clears thinking
    act(() => {
      ws.simulateDone();
    });

    expect(result.current.thinkingSteps).toEqual([]);
  });

  // ---- 10. submitClarification clears pending and sends raw ----

  it("submitClarification clears pending questions and sends response", () => {
    const { result } = renderChat();

    // Simulate clarification questions
    act(() => {
      ws.simulateClarification([
        {
          question: "Which band?",
          header: "Band",
          options: [
            { label: "B04", description: "Red" },
            { label: "B08", description: "NIR" },
          ],
        },
      ]);
    });

    expect(result.current.pendingClarification).toHaveLength(1);

    // Submit answer
    act(() => {
      result.current.submitClarification({ "Which band?": "B04" });
    });

    expect(result.current.pendingClarification).toBeNull();
    expect(ws.sentRaw).toHaveLength(1);
    expect(ws.sentRaw[0]).toEqual({
      type: "clarification_response",
      answers: { "Which band?": "B04" },
    });
  });

  // ---- Saved-result viz bundle: replay into tabs + flush on done ----

  it("sends the open question's id with the answer", () => {
    const { result } = renderChat();
    act(() => {
      ws.callbacks.onClarification?.([{ question: "Which year?", header: "Year", options: [], multiSelect: false }], "q7");
    });
    act(() => {
      result.current.submitClarification({ "0": "2024" });
    });
    expect(ws.sentRaw[ws.sentRaw.length - 1]).toEqual({ type: "clarification_response", answers: { "0": "2024" }, question_id: "q7" });
  });

  describe("grounding warnings and sources", () => {
    it("attaches them to this turn's answer, sources once the turn ends", () => {
      const { result } = renderChat();
      expect(ws.callbacks.onCitations).toBeDefined();
      expect(ws.callbacks.onGroundingWarning).toBeDefined();
      act(() => {
        result.current.sendMessage("first");
        ws.simulateTextStream(["old answer"]);
        ws.simulateDone();
        result.current.sendMessage("second");
        ws.callbacks.onCitations?.(["https://a.example", "https://a.example", "https://b.example"]);
        ws.simulateTextStream(["new answer"]);
        ws.callbacks.onGroundingWarning?.([{ value: 87, token: "87%", context: "c" }]);
        ws.simulateDone();
      });
      const [, firstAnswer, , secondAnswer] = result.current.messages;
      expect(firstAnswer!.sources).toBeUndefined();
      expect(firstAnswer!.groundingWarnings).toBeUndefined();
      expect(secondAnswer!.sources).toEqual(["https://a.example", "https://b.example"]);
      expect(secondAnswer!.groundingWarnings).toEqual([{ value: 87, token: "87%", context: "c" }]);
      const ui = result.current.uiMessages[3]!;
      expect(ui.metadata?.sources).toHaveLength(2);
    });

    it("never attaches to the previous turn when this turn has no answer yet", () => {
      const { result } = renderChat();
      expect(ws.callbacks.onCitations).toBeDefined();
      act(() => {
        result.current.sendMessage("first");
        ws.simulateTextStream(["old answer"]);
        ws.simulateDone();
        result.current.sendMessage("second");
        ws.callbacks.onGroundingWarning?.([{ value: 1, token: "1%", context: "c" }]);
        ws.callbacks.onCitations?.(["https://a.example"]);
        ws.simulateDone();
      });
      expect(result.current.messages[1]!.groundingWarnings).toBeUndefined();
      expect(result.current.messages[1]!.sources).toBeUndefined();
    });
  });

  describe("saved-result viz bundle", () => {
    it("replayed report_sections populate reportSections (normalized)", () => {
      const { result } = renderChat();

      act(() => {
        ws.callbacks.onReportSections?.([
          {
            type: "interpretation",
            title: "Interpretation",
            content: "Healthy vegetation",
            timestamp: "2026-06-24T00:00:00.000Z",
          },
        ]);
      });

      expect(result.current.reportSections).toHaveLength(1);
      const section = result.current.reportSections[0]!;
      expect(section.title).toBe("Interpretation");
      expect(section.id).toBeTruthy();
      expect(section.timestamp).toBeInstanceOf(Date);
    });

    it("replayed chart visualization populates visualizations", () => {
      const { result } = renderChat();

      act(() => {
        ws.callbacks.onVisualization?.({
          id: "chart-1",
          type: "chart",
          title: "NDVI Time Series",
          data: { chartType: "line", series: [{ name: "NDVI", values: [0.3, 0.5] }] },
        });
      });

      const charts = result.current.visualizations.filter((v) => v.type === "chart");
      expect(charts).toHaveLength(1);
      expect(charts[0]!.title).toBe("NDVI Time Series");
    });

    it("flushes non-map viz + report sections to the saved result on done", () => {
      const calls: Array<{ url: string; init: RequestInit }> = [];
      const realFetch = globalThis.fetch;
      globalThis.fetch = ((url: string, init: RequestInit) => {
        calls.push({ url, init });
        return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      }) as typeof fetch;

      try {
        const { result } = renderChat({ authToken: "tok-123" });

        act(() => {
          result.current.sendMessage("Show NDVI for Kerala");
        });

        // Map carries the save_id; chart is the related tab data this run
        act(() => {
          ws.callbacks.onVisualization?.({
            id: "map-1",
            type: "map",
            title: "NDVI",
            saveId: "abcd1234",
            data: { type: "raster", url: "/render-raster?path=/tmp/x.tif" },
          });
          ws.callbacks.onVisualization?.({
            id: "chart-1",
            type: "chart",
            title: "NDVI Time Series",
            data: { chartType: "line", series: [] },
          });
        });

        // A report-routed tool produces a report section this run
        act(() => {
          ws.simulateToolCall("interpret_result", { save_id: "abcd1234" }, "Looks healthy");
        });

        act(() => {
          ws.callbacks.onDone?.();
        });

        const vizPuts = calls.filter((c) => c.url.includes("/visualizations"));
        expect(vizPuts).toHaveLength(1);
        expect(vizPuts[0]!.url).toBe("/saved-jobs/abcd1234/visualizations");
        expect(vizPuts[0]!.init.method).toBe("PUT");
        expect((vizPuts[0]!.init.headers as Record<string, string>).Authorization).toBe(
          "Bearer tok-123"
        );
        const body = JSON.parse(vizPuts[0]!.init.body as string);
        // Bundle carries the chart + report, NOT the map (map reloads from GeoTIFF)
        expect(body.visualizations).toHaveLength(1);
        expect(body.visualizations[0].type).toBe("chart");
        expect(body.reportSections).toHaveLength(1);
        expect(body.reportSections[0].title).toBe("Interpretation");
      } finally {
        globalThis.fetch = realFetch;
      }
    });

    it("live report_sections (method card) join the flush bundle on done", () => {
      const calls: Array<{ url: string; init: RequestInit }> = [];
      const realFetch = globalThis.fetch;
      globalThis.fetch = ((url: string, init: RequestInit) => {
        calls.push({ url, init });
        return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      }) as typeof fetch;

      try {
        const { result } = renderChat({ authToken: "tok-123" });

        act(() => {
          result.current.sendMessage("decision-grade ndvi for Kings County");
        });

        act(() => {
          ws.callbacks.onVisualization?.({
            id: "map-1",
            type: "map",
            title: "NDVI",
            saveId: "abcd1234",
            data: { type: "raster", url: "/render-raster?path=/tmp/x.tif" },
          });
          // The bridge decision-grade route streams the Method Card live.
          // The map viz above supplies the run's saved result: this pins the
          // bundle-membership contract, which engages whenever a run carries
          // a saveId (decision-grade runs produce none, so the flush
          // stays idle there until that route saves an artifact).
          ws.callbacks.onReportSections?.(
            [
              {
                type: "method_card",
                title: "Method Card",
                content: "## Method Card\n- **plan_hash:** abc123",
                timestamp: "2026-07-22T00:00:00.000Z",
                toolName: "agentic_decision_query",
              },
            ],
            true
          );
        });

        act(() => {
          ws.callbacks.onDone?.();
        });

        // Rendered in the Report tab…
        expect(result.current.reportSections).toHaveLength(1);
        expect(result.current.reportSections[0]!.type).toBe("method_card");
        // …and persisted with the run's bundle
        const vizPuts = calls.filter((c) => c.url.includes("/visualizations"));
        expect(vizPuts).toHaveLength(1);
        const body = JSON.parse(vizPuts[0]!.init.body as string);
        expect(body.reportSections).toHaveLength(1);
        expect(body.reportSections[0].type).toBe("method_card");
        expect(body.reportSections[0].title).toBe("Method Card");
      } finally {
        globalThis.fetch = realFetch;
      }
    });

    it("replayed (non-live) report_sections stay out of the flush bundle", () => {
      const calls: Array<{ url: string }> = [];
      const realFetch = globalThis.fetch;
      globalThis.fetch = ((url: string) => {
        calls.push({ url: String(url) });
        return Promise.resolve(new Response("{}", { status: 200 }));
      }) as typeof fetch;

      try {
        const { result } = renderChat();

        act(() => {
          result.current.sendMessage("Show NDVI for Kerala");
        });

        act(() => {
          ws.callbacks.onVisualization?.({
            id: "map-1",
            type: "map",
            title: "NDVI",
            saveId: "abcd1234",
            data: { type: "raster", url: "/render-raster?path=/tmp/x.tif" },
          });
          // Saved-result replay: same event type, no live flag
          ws.callbacks.onReportSections?.([
            {
              type: "interpretation",
              title: "Interpretation",
              content: "Healthy vegetation",
              timestamp: "2026-06-24T00:00:00.000Z",
            },
          ]);
          ws.callbacks.onDone?.();
        });

        // Rendered, but NOT re-persisted (would duplicate the saved bundle)
        expect(result.current.reportSections).toHaveLength(1);
        expect(calls.filter((c) => c.url.includes("/visualizations"))).toHaveLength(0);
      } finally {
        globalThis.fetch = realFetch;
      }
    });

    it("replayed visualizations render but stay out of the flush bundle", () => {
      const calls: Array<{ url: string; init?: RequestInit }> = [];
      const realFetch = globalThis.fetch;
      globalThis.fetch = ((url: string, init: RequestInit) => {
        calls.push({ url: String(url), init });
        return Promise.resolve(new Response("{}", { status: 200 }));
      }) as typeof fetch;

      try {
        const { result } = renderChat({ authToken: "tok-123" });

        act(() => {
          result.current.sendMessage("Show NDVI for Kerala");
        });

        act(() => {
          // Live run output: map carrying the run's save id + a chart
          ws.callbacks.onVisualization?.({
            id: "map-1",
            type: "map",
            title: "NDVI",
            saveId: "newsave1",
            data: { type: "raster", url: "/render-raster?path=/tmp/x.tif" },
          });
          ws.callbacks.onVisualization?.({
            id: "chart-1",
            type: "chart",
            title: "Live chart",
            data: { chartType: "line", series: [] },
          });
          // Mid-run "Load on Map" replay of an old save: same event type,
          // replay=true (web_interface marks the map and the bundle viz)
          ws.callbacks.onVisualization?.(
            {
              id: "map-2",
              type: "map",
              title: "Old map",
              saveId: "oldsave1",
              data: { type: "raster", url: "/render-raster?path=/tmp/old.tif" },
            },
            true,
          );
          ws.callbacks.onVisualization?.(
            {
              id: "chart-2",
              type: "chart",
              title: "Old chart",
              data: { chartType: "line", series: [] },
            },
            true,
          );
        });

        act(() => {
          ws.callbacks.onDone?.();
        });

        // Replayed viz still render alongside the live ones
        expect(result.current.visualizations).toHaveLength(4);
        // Exactly one PUT — to the running turn's save; the old save is untouched
        const vizPuts = calls.filter((c) => c.url.includes("/visualizations"));
        expect(vizPuts).toHaveLength(1);
        expect(vizPuts[0]!.url).toBe("/saved-jobs/newsave1/visualizations");
        const body = JSON.parse(vizPuts[0]!.init!.body as string);
        // The bundle carries only the live chart — no cross-contamination
        expect(body.visualizations).toHaveLength(1);
        expect(body.visualizations[0].title).toBe("Live chart");
      } finally {
        globalThis.fetch = realFetch;
      }
    });

    it("does not flush when the run produced no saved result", () => {
      const calls: string[] = [];
      const realFetch = globalThis.fetch;
      globalThis.fetch = ((url: string) => {
        calls.push(String(url));
        return Promise.resolve(new Response("{}", { status: 200 }));
      }) as typeof fetch;

      try {
        const { result } = renderChat();
        act(() => {
          result.current.sendMessage("Just chatting");
        });
        // Chart but no map/save_id this run
        act(() => {
          ws.callbacks.onVisualization?.({
            id: "chart-1",
            type: "chart",
            title: "Chart",
            data: { chartType: "line", series: [] },
          });
          ws.callbacks.onDone?.();
        });
        expect(calls.filter((u) => u.includes("/visualizations"))).toHaveLength(0);
      } finally {
        globalThis.fetch = realFetch;
      }
    });
  });
});
