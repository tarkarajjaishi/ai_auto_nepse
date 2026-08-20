"use client";

import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Cell, Row } from "@/lib/api";
import { decisionTone, formatCell, gradeTone, isNumericCol, TONE_CLASS } from "@/lib/format";

/**
 * One table for every board.
 *
 * Columns are DERIVED from the API's `columns` array rather than declared per page. The boards
 * change shape whenever a Python script gains a field, and hand-maintained column lists in the
 * frontend would rot silently — which is the same class of bug as ui.py reading `int(r[9])` and
 * hoping the header never moved.
 *
 * `priority` names the handful of columns worth showing first on a narrow screen; everything
 * else stays in the table but scrolls.
 */
export type BoardTableProps = {
  rows: Row[];
  columns: string[];
  priority?: string[];
  /** columns to hide entirely — usually ones the page renders itself, like the session date */
  hide?: string[];
  onRowClick?: (row: Row) => void;
  emptyMessage?: string;
};

const TONED: Record<string, (v: Cell) => ReturnType<typeof decisionTone>> = {
  decision: decisionTone,
  verdict: decisionTone,
  signal: decisionTone,
  grade: gradeTone,
  trend: (v) => (String(v) === "Bullish" ? "up" : String(v) === "Bearish" ? "down" : "none"),
  performer: (v) => (String(v) === "AVOID" ? "down" : String(v).includes("ELITE") ? "up" : "none"),
  pullback: decisionTone,
  side: (v) => (String(v) === "BUY" ? "up" : String(v) === "SELL" ? "down" : "none"),
};

export function BoardTable({
  rows,
  columns,
  priority,
  hide = [],
  onRowClick,
  emptyMessage = "Nothing in this view.",
}: BoardTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState("");

  const visible = useMemo(
    () => columns.filter((c) => !hide.includes(c)),
    [columns, hide],
  );

  const ordered = useMemo(() => {
    if (!priority) return visible;
    const head = priority.filter((c) => visible.includes(c));
    return [...head, ...visible.filter((c) => !head.includes(c))];
  }, [visible, priority]);

  const defs = useMemo<ColumnDef<Row>[]>(
    () =>
      ordered.map((col) => ({
        id: col,
        accessorFn: (r) => r[col],
        header: col.replace(/_/g, " "),
        cell: ({ getValue }) => {
          const v = getValue() as Cell;
          const tone = TONED[col]?.(v) ?? "none";
          return (
            <span className={cn("tabular-nums", TONE_CLASS[tone], tone !== "none" && "font-medium")}>
              {formatCell(col, v)}
            </span>
          );
        },
        sortingFn: isNumericCol(col, rows) ? "basic" : "alphanumeric",
        meta: { numeric: isNumericCol(col, rows) },
      })),
    [ordered, rows],
  );

  const table = useReactTable({
    data: rows,
    columns: defs,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const visibleRows = table.getRowModel().rows;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Search className="size-3.5 shrink-0 text-muted-foreground" />
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter…"
          className="h-7 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
        />
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {visibleRows.length}
          {visibleRows.length !== rows.length && ` / ${rows.length}`}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-card">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-border">
                {hg.headers.map((h) => {
                  const numeric = (h.column.columnDef.meta as { numeric?: boolean })?.numeric;
                  const dir = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      onClick={h.column.getToggleSortingHandler()}
                      className={cn(
                        "group cursor-pointer whitespace-nowrap px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground select-none hover:text-foreground",
                        numeric ? "text-right" : "text-left",
                      )}
                    >
                      <span className="inline-flex items-center gap-1">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {dir === "asc" ? (
                          <ArrowUp className="size-3 text-primary" />
                        ) : dir === "desc" ? (
                          <ArrowDown className="size-3 text-primary" />
                        ) : (
                          <ChevronsUpDown className="size-3 opacity-0 transition-opacity group-hover:opacity-40" />
                        )}
                      </span>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr
                key={row.id}
                onClick={() => onRowClick?.(row.original)}
                className={cn(
                  "border-b border-border/50 transition-colors",
                  onRowClick && "cursor-pointer hover:bg-accent/50",
                )}
              >
                {row.getVisibleCells().map((c) => {
                  const numeric = (c.column.columnDef.meta as { numeric?: boolean })?.numeric;
                  const first = c.column.id === "symbol";
                  return (
                    <td
                      key={c.id}
                      className={cn(
                        "whitespace-nowrap px-3 py-1.5",
                        numeric ? "text-right font-mono" : "text-left",
                        first && "font-mono font-semibold",
                      )}
                    >
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>

        {visibleRows.length === 0 && (
          <p className="px-3 py-8 text-center text-[13px] text-muted-foreground">
            {rows.length === 0 ? emptyMessage : `Nothing matches “${filter}”.`}
          </p>
        )}
      </div>
    </div>
  );
}
