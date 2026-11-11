/**
 * OpenEO process-graph viewer (visual DAG + raw JSON toggle) with its
 * edge-resolution and topo-sort helpers. Extracted from
 * VisualizationPanel.tsx (TECH-DEBT-HOTSPOTS.md #10, step 2 - pure move).
 */
import { useState, useMemo } from "react";
import { Copy, Check, Code } from "lucide-react";
import { cn } from "@/lib/utils";
import { ProcessGraphVisualization, ProcessNode } from "@/types";

// Resolve from_node references to build edges automatically
function resolveEdges(
  nodes: ProcessNode[]
): { source: string; target: string; argument: string }[] {
  const edges: { source: string; target: string; argument: string }[] = [];
  for (const node of nodes) {
    for (const [argKey, argValue] of Object.entries(node.arguments)) {
      if (
        argValue &&
        typeof argValue === "object" &&
        "from_node" in (argValue as Record<string, unknown>)
      ) {
        const fromNode = (argValue as { from_node: string }).from_node;
        edges.push({ source: fromNode, target: node.id, argument: argKey });
      }
    }
  }
  return edges;
}

// Compute topological ordering for vertical layout
function topoSort(nodes: ProcessNode[], edges: { source: string; target: string }[]): string[] {
  const inDegree: Record<string, number> = {};
  const adj: Record<string, string[]> = {};
  for (const node of nodes) {
    inDegree[node.id] = 0;
    adj[node.id] = [];
  }
  for (const edge of edges) {
    if (adj[edge.source]) adj[edge.source]!.push(edge.target);
    inDegree[edge.target] = (inDegree[edge.target] || 0) + 1;
  }
  const queue: string[] = [];
  for (const id of Object.keys(inDegree)) {
    if (inDegree[id] === 0) queue.push(id);
  }
  const sorted: string[] = [];
  while (queue.length > 0) {
    const current = queue.shift()!;
    sorted.push(current);
    for (const neighbor of adj[current] || []) {
      inDegree[neighbor] = (inDegree[neighbor] || 0) - 1;
      if (inDegree[neighbor] === 0) queue.push(neighbor);
    }
  }
  // Add any remaining nodes (cycles or disconnected)
  for (const node of nodes) {
    if (!sorted.includes(node.id)) sorted.push(node.id);
  }
  return sorted;
}

export function ProcessGraphViewer({ data }: { data: ProcessGraphVisualization }) {
  const [expandedNode, setExpandedNode] = useState<string | null>(null);
  const [showRawJson, setShowRawJson] = useState(false);
  const [copied, setCopied] = useState(false);

  // Derive edges from from_node references if explicit edges are empty
  const edges = useMemo(() => {
    if (data.edges.length > 0) return data.edges;
    return resolveEdges(data.nodes);
  }, [data.nodes, data.edges]);

  const sortedIds = useMemo(() => topoSort(data.nodes, edges), [data.nodes, edges]);

  const nodeMap = useMemo(() => {
    const map: Record<string, ProcessNode> = {};
    for (const node of data.nodes) map[node.id] = node;
    return map;
  }, [data.nodes]);

  // Reconstruct raw graph JSON from nodes
  const rawGraphJson = useMemo(() => {
    const graph: Record<string, Record<string, unknown>> = {};
    for (const node of data.nodes) {
      const entry: Record<string, unknown> = {
        process_id: node.process_id,
        arguments: node.arguments,
      };
      if (node.result) entry.result = true;
      graph[node.id] = entry;
    }
    return JSON.stringify(graph, null, 2);
  }, [data.nodes]);

  // Extract summary info from load_collection node
  const summary = useMemo(() => {
    const loadNode = data.nodes.find((n) => n.process_id === "load_collection");
    if (!loadNode) return null;
    const args = loadNode.arguments;
    return {
      collection: args.id as string | undefined,
      bands: (args.bands as string[]) || [],
      bbox: args.spatial_extent as Record<string, number> | undefined,
      temporal: args.temporal_extent as string[] | undefined,
    };
  }, [data.nodes]);

  const handleCopyJson = async () => {
    try {
      await navigator.clipboard.writeText(rawGraphJson);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for non-secure contexts
      const textarea = document.createElement("textarea");
      textarea.value = rawGraphJson;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  // Layout constants
  const NODE_WIDTH = 280;
  const NODE_HEIGHT = 80;
  const EXPANDED_MIN_HEIGHT = 120;
  const VERTICAL_GAP = 48;
  const LEFT_PADDING = 40;
  const TOP_PADDING = 24;

  // Compute positions for each node
  const positions = useMemo(() => {
    const pos: Record<string, { x: number; y: number }> = {};
    let yOffset = TOP_PADDING;
    sortedIds.forEach((id) => {
      pos[id] = { x: LEFT_PADDING, y: yOffset };
      yOffset += (expandedNode === id ? EXPANDED_MIN_HEIGHT : NODE_HEIGHT) + VERTICAL_GAP;
    });
    return pos;
  }, [sortedIds, expandedNode]);

  const totalHeight = useMemo(() => {
    let h = TOP_PADDING;
    sortedIds.forEach((id) => {
      h += (expandedNode === id ? EXPANDED_MIN_HEIGHT : NODE_HEIGHT) + VERTICAL_GAP;
    });
    return h;
  }, [sortedIds, expandedNode]);
  const totalWidth = LEFT_PADDING * 2 + NODE_WIDTH;

  return (
    <div className="h-full overflow-auto rounded-md border bg-foreground/5 p-4">
      {/* Header with actions */}
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-medium">Process Graph</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopyJson}
            className="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            title="Copy process graph JSON"
          >
            {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
            {copied ? "Copied!" : "Copy JSON"}
          </button>
          <button
            onClick={() => setShowRawJson(!showRawJson)}
            className={cn(
              "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors hover:bg-accent hover:text-accent-foreground",
              showRawJson ? "bg-accent text-accent-foreground" : "bg-card text-muted-foreground"
            )}
            title="Toggle raw JSON view"
          >
            <Code className="h-3 w-3" />
            {showRawJson ? "Visual" : "Raw JSON"}
          </button>
        </div>
      </div>

      {/* Summary bar */}
      {summary && (
        <div className="mb-4 flex flex-wrap gap-2 rounded-md border bg-card p-2 text-xs text-muted-foreground">
          {summary.collection && (
            <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-primary">
              {summary.collection}
            </span>
          )}
          {summary.bands.length > 0 && <span>Bands: {summary.bands.join(", ")}</span>}
          {summary.temporal && summary.temporal.length >= 2 && (
            <span>
              {summary.temporal[0]} to {summary.temporal[1]}
            </span>
          )}
          {summary.bbox && (
            <span className="font-mono text-[10px]">
              [{summary.bbox.west?.toFixed(2)}, {summary.bbox.south?.toFixed(2)},{" "}
              {summary.bbox.east?.toFixed(2)}, {summary.bbox.north?.toFixed(2)}]
            </span>
          )}
        </div>
      )}

      {/* Raw JSON view */}
      {showRawJson ? (
        <pre className="overflow-auto rounded-md border bg-card p-4 font-mono text-xs text-foreground">
          {rawGraphJson}
        </pre>
      ) : (
        <div className="relative" style={{ minHeight: totalHeight, minWidth: totalWidth }}>
          {/* SVG arrows layer */}
          <svg
            className="pointer-events-none absolute left-0 top-0"
            width={totalWidth}
            height={totalHeight}
            style={{ zIndex: 0 }}
          >
            <defs>
              <marker
                id="arrowhead"
                markerWidth="8"
                markerHeight="6"
                refX="8"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" className="fill-muted-foreground" />
              </marker>
            </defs>
            {edges.map((edge: { source: string; target: string; argument: string }, i: number) => {
              const srcPos = positions[edge.source];
              const tgtPos = positions[edge.target];
              if (!srcPos || !tgtPos) return null;

              const srcHeight = expandedNode === edge.source ? EXPANDED_MIN_HEIGHT : NODE_HEIGHT;
              const x1 = srcPos.x + NODE_WIDTH / 2;
              const y1 = srcPos.y + srcHeight;
              const x2 = tgtPos.x + NODE_WIDTH / 2;
              const y2 = tgtPos.y;

              // Bezier curve for nicer lines
              const midY = (y1 + y2) / 2;

              return (
                <g key={i}>
                  <path
                    d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
                    className="process-edge"
                    markerEnd="url(#arrowhead)"
                  />
                  {/* Edge label */}
                  <text
                    x={(x1 + x2) / 2 + 8}
                    y={midY}
                    className="fill-muted-foreground text-[10px]"
                    dominantBaseline="middle"
                  >
                    {edge.argument}
                  </text>
                </g>
              );
            })}
          </svg>

          {/* Node cards layer */}
          {sortedIds.map((id) => {
            const node = nodeMap[id];
            if (!node) return null;
            const pos = positions[id];
            if (!pos) return null;
            const isExpanded = expandedNode === id;
            const isResultNode = node.result === true;

            // Check if this node has a from_node dependency to highlight
            const incomingEdges = edges.filter((e: { target: string }) => e.target === id);

            // Filter arguments for display (skip from_node refs)
            const displayArgs = Object.entries(node.arguments).filter(([, value]) => {
              if (
                value &&
                typeof value === "object" &&
                "from_node" in (value as Record<string, unknown>)
              )
                return false;
              return true;
            });

            return (
              <div
                key={id}
                className={cn(
                  "absolute cursor-pointer rounded-lg border bg-card shadow-sm transition-all hover:shadow-md",
                  isResultNode && "ring-2 ring-primary"
                )}
                style={{
                  left: pos.x,
                  top: pos.y,
                  width: NODE_WIDTH,
                  minHeight: isExpanded ? EXPANDED_MIN_HEIGHT : NODE_HEIGHT,
                  height: isExpanded ? "auto" : NODE_HEIGHT,
                  zIndex: isExpanded ? 10 : 1,
                }}
                onClick={() => setExpandedNode(isExpanded ? null : id)}
              >
                <div className="flex items-start gap-2 p-3">
                  <div className="shrink-0 rounded bg-primary/10 px-2 py-1 font-mono text-xs text-primary">
                    {id}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{node.process_id}</span>
                      {isResultNode && (
                        <span className="shrink-0 rounded bg-primary px-1.5 py-0.5 text-[9px] font-semibold text-primary-foreground">
                          result
                        </span>
                      )}
                    </div>
                    <div className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
                      {(isExpanded ? displayArgs : displayArgs.slice(0, 2)).map(([key, value]) => (
                        <div key={key} className={isExpanded ? "break-all" : "truncate"}>
                          <span className="font-mono">{key}:</span>{" "}
                          {typeof value === "object"
                            ? isExpanded
                              ? JSON.stringify(value, null, 2)
                              : JSON.stringify(value).slice(0, 30) + "..."
                            : String(value)}
                        </div>
                      ))}
                      {!isExpanded && displayArgs.length > 2 && (
                        <div className="italic text-primary/50">
                          +{displayArgs.length - 2} more...
                        </div>
                      )}
                      {incomingEdges.length > 0 && (
                        <div className="text-primary/70">
                          {incomingEdges.map((e: { source: string }) => e.source).join(", ")} {"->"}{" "}
                          here
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
