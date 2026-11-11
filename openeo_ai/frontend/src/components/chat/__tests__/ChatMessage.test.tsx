/**
 * ChatMessage: grounding warnings and sources render under an assistant answer.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatMessage } from "../ChatMessage";
import type { UIMessage } from "@/types";

function answer(metadata: UIMessage["metadata"]): UIMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "About 87% of the field is green.",
    parts: [{ type: "text", text: "About 87% of the field is green." }],
    createdAt: new Date(),
    metadata,
  } as UIMessage;
}

describe("ChatMessage trust and provenance", () => {
  it("flags numbers no tool result backs", () => {
    render(<ChatMessage message={answer({ groundingWarnings: [{ value: 87, token: "87%", context: "c" }] })} />);
    expect(screen.getByTestId("grounding-warning")).toHaveTextContent("87%");
  });

  it("lists sources, linking only web addresses", () => {
    render(<ChatMessage message={answer({ sources: ["https://earth-search.example/v1", "local note"] })} />);
    const box = screen.getByTestId("message-sources");
    const link = screen.getByRole("link", { name: "https://earth-search.example/v1" });
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(box).toHaveTextContent("local note");
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("shows neither when there is nothing to show", () => {
    render(<ChatMessage message={answer(undefined)} />);
    expect(screen.queryByTestId("grounding-warning")).toBeNull();
    expect(screen.queryByTestId("message-sources")).toBeNull();
  });
});
