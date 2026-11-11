// ABOUTME: LangfuseWeb client singleton for browser-side score emission (Phase D).

/**
 * LangfuseWeb is the browser-safe client. It uses the PUBLIC key only —
 * never the secret key — so it's safe to ship to end users.
 *
 * Used for:
 *  - User feedback scores (thumbs up/down) posted directly to Langfuse
 *  - Implicit feedback scores (copy-to-clipboard, etc.)
 *
 * If VITE_LANGFUSE_PUBLIC_KEY is not set (e.g. dev environment without
 * tracing), the `scoreTrace()` helper below becomes a no-op so the chat
 * UI keeps working.
 */

import { LangfuseWeb } from "langfuse";

const publicKey = import.meta.env.VITE_LANGFUSE_PUBLIC_KEY as string | undefined;
const baseUrl = import.meta.env.VITE_LANGFUSE_HOST as string | undefined;

let client: LangfuseWeb | null = null;

if (publicKey && baseUrl) {
  try {
    client = new LangfuseWeb({
      publicKey,
      baseUrl,
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[langfuse] failed to init LangfuseWeb:", err);
    client = null;
  }
} else {
  // eslint-disable-next-line no-console
  console.info("[langfuse] disabled — VITE_LANGFUSE_PUBLIC_KEY or VITE_LANGFUSE_HOST not set");
}

/**
 * Emit a score to a Langfuse trace. Safe no-op when tracing is disabled.
 *
 * @param traceId  The trace ID to attach the score to (from WS `trace` event).
 * @param name     Score dimension name (e.g. "user-thumbs", "implicit-copy").
 * @param value    Score value. For BOOLEAN data: 1 = true, 0 = false.
 * @param dataType "BOOLEAN" | "NUMERIC" | "CATEGORICAL".
 * @param comment  Optional free-text note.
 */
export function scoreTrace(
  traceId: string | undefined,
  name: string,
  value: number | string,
  dataType: "BOOLEAN" | "NUMERIC" | "CATEGORICAL" = "NUMERIC",
  comment?: string
): void {
  if (!client || !traceId) return;
  try {
    client.score({
      traceId,
      name,
      value: value as number,
      dataType,
      comment,
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[langfuse] scoreTrace failed:", err);
  }
}
