/**
 * Sortable data table for the Results panel Table tab. Extracted from
 * VisualizationPanel.tsx (TECH-DEBT-HOTSPOTS.md #10, step 2 - pure move).
 */
import { useState, useMemo } from "react";
import { Copy } from "lucide-react";
import { Visualization, TableVisualization } from "@/types";

export function DataTable({ visualization }: { visualization: Visualization }) {
  const data = visualization.data as TableVisualization;
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // Sort rows. Hooks must run unconditionally on every render — the
  // empty-data return sits below so a mounted table can update between
  // empty and populated data without changing the hook count.
  const sortedRows = useMemo(() => {
    const rows = data?.rows ?? [];
    if (sortCol === null) return rows;
    return [...rows].sort((a, b) => {
      const av = a[sortCol] ?? "";
      const bv = b[sortCol] ?? "";
      if (typeof av === "number" && typeof bv === "number") {
        return sortDir === "asc" ? av - bv : bv - av;
      }
      return sortDir === "asc"
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [data?.rows, sortCol, sortDir]);

  if (!data?.headers?.length) {
    return (
      <div className="flex items-center justify-center p-8 text-muted-foreground">
        No table data
      </div>
    );
  }

  // Detect numeric columns
  const isNumericCol = data.headers.map((_, ci) =>
    data.rows.some((row) => typeof row[ci] === "number")
  );

  const handleSort = (colIndex: number) => {
    if (sortCol === colIndex) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(colIndex);
      setSortDir("asc");
    }
  };

  const formatCell = (cell: string | number | null | undefined) => {
    if (cell === null || cell === undefined) return "\u2014";
    if (typeof cell === "number") return cell.toFixed(4);
    return String(cell);
  };

  const handleCopyCSV = () => {
    const csv = [
      data.headers.join(","),
      ...sortedRows.map((row) => row.map((c) => formatCell(c)).join(",")),
    ].join("\n");
    navigator.clipboard.writeText(csv).catch(() => {});
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-3 py-1.5">
        <span className="text-xs text-muted-foreground">{sortedRows.length} rows</span>
        <button
          onClick={handleCopyCSV}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <Copy className="h-3 w-3" /> Copy CSV
        </button>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm" aria-label={visualization.title || "Data table"}>
          <thead className="sticky top-0 z-10 bg-muted/80 backdrop-blur-sm">
            <tr>
              {data.headers.map((header, i) => (
                <th
                  key={i}
                  scope="col"
                  className={`cursor-pointer select-none whitespace-nowrap px-3 py-2 text-left text-xs font-medium uppercase tracking-wider hover:bg-muted ${isNumericCol[i] ? "text-right" : ""}`}
                  onClick={() => handleSort(i)}
                  aria-sort={
                    sortCol === i ? (sortDir === "asc" ? "ascending" : "descending") : "none"
                  }
                >
                  {header}
                  {sortCol === i && (sortDir === "asc" ? " \u2191" : " \u2193")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {sortedRows.map((row, ri) => (
              <tr key={ri} className="hover:bg-muted/50">
                {data.headers.map((_, ci) => (
                  <td
                    key={ci}
                    className={`whitespace-nowrap px-3 py-1.5 text-xs ${isNumericCol[ci] ? "text-right tabular-nums" : ""}`}
                  >
                    {formatCell(row[ci])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
