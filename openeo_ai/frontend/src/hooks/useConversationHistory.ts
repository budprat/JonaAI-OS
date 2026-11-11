/**
 * useConversationHistory -- Manages persistent conversation history in localStorage.
 *
 * Stores an index of conversation summaries at `jonaai-conversations` and
 * individual conversation message arrays at `jonaai-conv-{id}`.
 *
 * Design constraints:
 *   - Max 50 messages per conversation (oldest trimmed on save)
 *   - Max 20 conversations in the index (oldest archived conversations pruned)
 *   - Visualization data stored as metadata only (id, type, title) to avoid quota issues
 *   - Total localStorage target: < 5MB
 */
import { useState, useCallback, useRef, useEffect } from "react";
import type { Message, Visualization } from "@/types";
import { generateId } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Lightweight visualization metadata stored in localStorage (no heavy raster data). */
export interface VisualizationMeta {
  id: string;
  type: string;
  title?: string;
  saveId?: string;
}

export interface ConversationSummary {
  id: string;
  /** First user message, truncated to 80 chars. */
  title: string;
  messageCount: number;
  createdAt: string;
  lastMessageAt: string;
  /** Lightweight viz metadata for display purposes. */
  visualizations?: VisualizationMeta[];
}

/** Serializable message for localStorage (Date -> ISO string). */
interface StoredMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  toolName?: string;
  isRetryable?: boolean;
}

export interface UseConversationHistoryReturn {
  /** Index of all saved conversations (most recent first). */
  conversations: ConversationSummary[];
  /** Currently active conversation ID (null = new unsaved conversation). */
  activeConversationId: string | null;
  /** Save/update the current conversation. Returns the conversation ID. */
  saveConversation: (
    messages: Message[],
    visualizations?: Visualization[],
  ) => string;
  /** Archive the current conversation and clear the active ID. */
  archiveAndStartNew: (
    messages: Message[],
    visualizations?: Visualization[],
  ) => void;
  /** Load a conversation's messages from storage. */
  loadConversation: (id: string) => Message[] | null;
  /** Set a conversation as active (for resuming). */
  setActiveConversation: (id: string) => void;
  /** Delete a single conversation. */
  deleteConversation: (id: string) => void;
  /** Clear all conversation history. */
  clearAll: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CONVERSATIONS_INDEX_KEY = "jonaai-conversations";
const CONVERSATION_PREFIX = "jonaai-conv-";
const ACTIVE_CONVERSATION_KEY = "jonaai-active-conv";
const MAX_MESSAGES_PER_CONVERSATION = 50;
const MAX_CONVERSATIONS = 20;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn(`[ConversationHistory] Failed to write "${key}":`, error);
  }
}

/** Convert Message[] to a storage-safe format (Date -> ISO string, strip heavy fields). */
function serializeMessages(messages: Message[]): StoredMessage[] {
  return messages
    .filter((m) => m.role !== "tool") // Skip tool messages (heavy + reconstructable)
    .slice(-MAX_MESSAGES_PER_CONVERSATION)
    .map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      timestamp:
        m.timestamp instanceof Date
          ? m.timestamp.toISOString()
          : String(m.timestamp),
      ...(m.toolName ? { toolName: m.toolName } : {}),
      ...(m.isRetryable ? { isRetryable: m.isRetryable } : {}),
    }));
}

/** Convert stored messages back to Message objects (ISO string -> Date). */
function deserializeMessages(stored: StoredMessage[]): Message[] {
  return stored.map((s) => ({
    id: s.id,
    role: s.role,
    content: s.content,
    timestamp: new Date(s.timestamp),
    ...(s.toolName ? { toolName: s.toolName } : {}),
    ...(s.isRetryable ? { isRetryable: s.isRetryable } : {}),
  }));
}

/** Extract lightweight viz metadata from full Visualization objects. */
function extractVizMeta(visualizations: Visualization[]): VisualizationMeta[] {
  return visualizations.map((v) => ({
    id: v.id,
    type: v.type,
    title: v.title,
    ...(v.saveId ? { saveId: v.saveId } : {}),
  }));
}

/** Derive a title from messages: first user message truncated to 80 chars. */
function deriveTitle(messages: Message[]): string {
  const firstUserMsg = messages.find((m) => m.role === "user");
  if (!firstUserMsg) return "New conversation";
  const text = firstUserMsg.content.trim();
  return text.length > 80 ? text.slice(0, 77) + "..." : text;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useConversationHistory(): UseConversationHistoryReturn {
  const [conversations, setConversations] = useState<ConversationSummary[]>(
    () => readJson<ConversationSummary[]>(CONVERSATIONS_INDEX_KEY, []),
  );
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(() => readJson<string | null>(ACTIVE_CONVERSATION_KEY, null));

  // Keep a ref to avoid stale closures in callbacks
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;

  // Persist index whenever it changes
  useEffect(() => {
    writeJson(CONVERSATIONS_INDEX_KEY, conversations);
  }, [conversations]);

  // Persist active conversation ID whenever it changes
  useEffect(() => {
    if (activeConversationId === null) {
      window.localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
    } else {
      writeJson(ACTIVE_CONVERSATION_KEY, activeConversationId);
    }
  }, [activeConversationId]);

  const saveConversation = useCallback(
    (messages: Message[], visualizations?: Visualization[]): string => {
      // Only save if there are user or assistant messages worth persisting
      const meaningful = messages.filter(
        (m) => m.role === "user" || m.role === "assistant",
      );
      if (meaningful.length === 0) {
        return activeConversationId ?? generateId();
      }

      const id = activeConversationId ?? generateId();
      const now = new Date().toISOString();

      // Save message data
      const stored = serializeMessages(messages);
      writeJson(`${CONVERSATION_PREFIX}${id}`, stored);

      // Update index
      const summary: ConversationSummary = {
        id,
        title: deriveTitle(messages),
        messageCount: meaningful.length,
        createdAt:
          conversationsRef.current.find((c) => c.id === id)?.createdAt ?? now,
        lastMessageAt: now,
        visualizations: visualizations
          ? extractVizMeta(visualizations)
          : undefined,
      };

      setConversations((prev) => {
        const filtered = prev.filter((c) => c.id !== id);
        const updated = [summary, ...filtered].slice(0, MAX_CONVERSATIONS);
        return updated;
      });

      setActiveConversationId(id);
      return id;
    },
    [activeConversationId],
  );

  const loadConversation = useCallback((id: string): Message[] | null => {
    const stored = readJson<StoredMessage[] | null>(
      `${CONVERSATION_PREFIX}${id}`,
      null,
    );
    if (!stored) return null;
    return deserializeMessages(stored);
  }, []);

  const setActiveConversation = useCallback((id: string) => {
    setActiveConversationId(id);
  }, []);

  const archiveAndStartNew = useCallback(
    (messages: Message[], visualizations?: Visualization[]) => {
      // Save current conversation if it has meaningful content
      const meaningful = messages.filter(
        (m) => m.role === "user" || m.role === "assistant",
      );
      if (meaningful.length > 0) {
        // Force save with current active ID, then clear
        const id = activeConversationId ?? generateId();
        const now = new Date().toISOString();
        const stored = serializeMessages(messages);
        writeJson(`${CONVERSATION_PREFIX}${id}`, stored);

        const summary: ConversationSummary = {
          id,
          title: deriveTitle(messages),
          messageCount: meaningful.length,
          createdAt:
            conversationsRef.current.find((c) => c.id === id)?.createdAt ?? now,
          lastMessageAt: now,
          visualizations: visualizations
            ? extractVizMeta(visualizations)
            : undefined,
        };

        setConversations((prev) => {
          const filtered = prev.filter((c) => c.id !== id);
          return [summary, ...filtered].slice(0, MAX_CONVERSATIONS);
        });
      }

      // Clear active conversation so next save creates a new one
      setActiveConversationId(null);
    },
    [activeConversationId],
  );

  const deleteConversation = useCallback((id: string) => {
    try {
      window.localStorage.removeItem(`${CONVERSATION_PREFIX}${id}`);
    } catch {
      // Ignore removal errors
    }
    setConversations((prev) => prev.filter((c) => c.id !== id));
    setActiveConversationId((current) => (current === id ? null : current));
  }, []);

  const clearAll = useCallback(() => {
    // Remove all conversation data from localStorage
    const current = conversationsRef.current;
    for (const conv of current) {
      try {
        window.localStorage.removeItem(`${CONVERSATION_PREFIX}${conv.id}`);
      } catch {
        // Ignore
      }
    }
    try {
      window.localStorage.removeItem(CONVERSATIONS_INDEX_KEY);
      window.localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
    } catch {
      // Ignore
    }
    setConversations([]);
    setActiveConversationId(null);
  }, []);

  return {
    conversations,
    activeConversationId,
    saveConversation,
    archiveAndStartNew,
    loadConversation,
    setActiveConversation,
    deleteConversation,
    clearAll,
  };
}
