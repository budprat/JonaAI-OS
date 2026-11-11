import { useState, useCallback, useRef, useEffect } from "react";
import { BBox } from "@/types";
import { ToastAction } from "@/components/ui/toast";
import { toast } from "@/hooks/use-toast";
import type { useUndoStack } from "@/hooks/useUndoStack";

export interface BboxSelection {
  /** The currently selected bounding box (or null). */
  selectedBbox: BBox | null;
  /**
   * Ref that always holds the latest bbox value.
   * Useful for reading inside callbacks without stale closures.
   */
  selectedBboxRef: React.RefObject<BBox | null>;
  /**
   * Set or clear the bounding box.
   * When clearing (bbox=null) and a previous bbox existed,
   * the action is pushed onto the undo stack with a toast.
   */
  handleBboxChange: (bbox: BBox | null) => void;
}

/**
 * useBboxSelection - Manages bounding box selection state with undo support.
 *
 * Clearing a bounding box is an undoable action: the previous value is
 * saved, pushed onto the undo stack, and a toast with an "Undo" button
 * is shown.
 *
 * @param undoStack - The undo stack instance from useUndoStack.
 */
export function useBboxSelection(
  undoStack: ReturnType<typeof useUndoStack>,
): BboxSelection {
  const [selectedBbox, setSelectedBbox] = useState<BBox | null>(null);
  const selectedBboxRef = useRef<BBox | null>(null);

  useEffect(() => {
    selectedBboxRef.current = selectedBbox;
  }, [selectedBbox]);

  const handleBboxChange = useCallback(
    (bbox: BBox | null) => {
      const prevBbox = selectedBboxRef.current;
      if (bbox === null && prevBbox !== null) {
        // Clearing bbox — make it undoable
        const saved = { ...prevBbox };
        setSelectedBbox(null);
        undoStack.push({
          type: "clear_bbox",
          label: "Clear bounding box",
          undo: () => setSelectedBbox(saved),
        });
        toast({
          title: "Bounding box cleared",
          action: (
            <ToastAction altText="Undo" onClick={() => setSelectedBbox(saved)}>
              Undo
            </ToastAction>
          ),
        });
      } else {
        setSelectedBbox(bbox);
      }
    },
    [undoStack.push],
  );

  return { selectedBbox, selectedBboxRef, handleBboxChange };
}
