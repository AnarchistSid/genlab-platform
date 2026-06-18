import { useState, useMemo, useCallback, type ReactNode } from "react";
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  sortable?: boolean;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  onRowClick?: (row: T) => void;
  sortable?: boolean;
  className?: string;
}

type SortDirection = "asc" | "desc";

interface SortState {
  column: string;
  direction: SortDirection;
}

function getNestedValue(obj: unknown, key: string): unknown {
  if (obj == null || typeof obj !== "object") return undefined;
  return (obj as Record<string, unknown>)[key];
}

export function DataTable<T>({
  columns,
  data,
  onRowClick,
  sortable = false,
  className,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState | null>(null);

  const handleSort = useCallback(
    (columnKey: string) => {
      if (!sortable) return;
      setSort((prev) => {
        if (prev?.column === columnKey) {
          return prev.direction === "asc"
            ? { column: columnKey, direction: "desc" }
            : null;
        }
        return { column: columnKey, direction: "asc" };
      });
    },
    [sortable]
  );

  const sortedData = useMemo(() => {
    if (!sort) return data;
    const { column, direction } = sort;
    return [...data].sort((a, b) => {
      const aVal = getNestedValue(a, column);
      const bVal = getNestedValue(b, column);
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      // ``cmp`` is unconditionally assigned in both branches below, so
      // an initializer would be dead code (no-useless-assignment).
      let cmp: number;
      if (typeof aVal === "number" && typeof bVal === "number") {
        cmp = aVal - bVal;
      } else {
        cmp = String(aVal).localeCompare(String(bVal));
      }
      return direction === "asc" ? cmp : -cmp;
    });
  }, [data, sort]);

  function renderSortIcon(columnKey: string) {
    if (!sortable) return null;
    if (sort?.column !== columnKey) {
      return <ArrowUpDown className="size-3.5 text-muted-foreground/50" />;
    }
    return sort.direction === "asc" ? (
      <ArrowUp className="size-3.5" />
    ) : (
      <ArrowDown className="size-3.5" />
    );
  }

  return (
    <div className={cn("overflow-x-auto rounded-lg border", className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            {columns.map((col) => {
              const isSortable = sortable && col.sortable !== false;
              return (
                <th
                  key={col.key}
                  className={cn(
                    "px-4 py-3 text-left font-medium text-muted-foreground",
                    isSortable && "cursor-pointer select-none hover:text-foreground",
                    col.className
                  )}
                  onClick={isSortable ? () => handleSort(col.key) : undefined}
                >
                  <div className="flex items-center gap-1.5">
                    {col.header}
                    {isSortable && renderSortIcon(col.key)}
                  </div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedData.map((row, rowIndex) => (
            <tr
              key={(row as Record<string, unknown>).id as string || (row as Record<string, unknown>).record_id as string || rowIndex}
              className={cn(
                "border-b transition-colors hover:bg-muted/30",
                onRowClick && "cursor-pointer"
              )}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={cn("px-4 py-3", col.className)}
                >
                  {col.render
                    ? col.render(row)
                    : String(getNestedValue(row, col.key) ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
