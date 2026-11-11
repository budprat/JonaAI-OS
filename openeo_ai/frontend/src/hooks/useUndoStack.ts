/**
 * useUndoStack - Manages a stack of undoable actions.
 *
 * Supports layer removal, bbox clear, and visualization removal.
 * Integrates with the toast system to show "Undo" action buttons,
 * and with Ctrl+Z / Cmd+Z keyboard shortcuts.
 */
import { useState, useCallback, useRef } from "react";

export type UndoActionType = "remove_layer" | "clear_bbox" | "remove_viz";

export interface UndoAction {
  /** Discriminator for the action kind. */
  type: UndoActionType;
  /** Callback that reverses the action. */
  undo: () => void;
  /** Human-readable label shown in the toast (e.g. "Remove NDVI layer"). */
  label: string;
}

/**
 * A simple undo stack that stores reversible actions.
 * Actions are pushed when the user performs destructive operations
 * (layer removal, bbox clear, etc.) and can be undone via Ctrl+Z
 * or the undo toast button.
 *
 * @param maxSize Maximum number of undo actions to retain (default 10).
 */
export function useUndoStack(maxSize = 10) {
  const [stack, setStack] = useState<UndoAction[]>([]);

  // Ref keeps the undo callback stable across re-renders so the
  // keyboard listener doesn't need to re-register on every push.
  const stackRef = useRef(stack);
  stackRef.current = stack;

  const push = useCallback(
    (action: UndoAction) => {
      setStack((prev) => [...prev.slice(-(maxSize - 1)), action]);
    },
    [maxSize],
  );

  const undo = useCallback((): boolean => {
    const current = stackRef.current;
    if (current.length === 0) return false;
    const last = current[current.length - 1]!;
    last.undo();
    setStack((prev) => prev.slice(0, -1));
    return true;
  }, []);

  const clear = useCallback(() => {
    setStack([]);
  }, []);

  const canUndo = stack.length > 0;
  const lastLabel =
    stack.length > 0 ? stack[stack.length - 1]!.label : undefined;

  return { push, undo, clear, canUndo, lastLabel, size: stack.length };
}
