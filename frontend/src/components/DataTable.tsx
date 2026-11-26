import { useState, type ReactNode } from "react";
import { Empty } from "@/components/ui";

export interface Column<T> {
  key: string;
  header: string;
  className?: string;
  sort?: (row: T) => string | number;
  render: (row: T) => ReactNode;
}

export function DataTable<T extends { id: string }>({
  columns,
  rows,
  empty,
}: {
  columns: Column<T>[];
  rows: T[];
  empty: string;
}) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);
  const column = columns.find((item) => item.key === sort?.key);
  const sorted = [...rows];
  if (sort && column?.sort) {
    const sorter = column.sort;
    sorted.sort((left, right) => {
      const a = sorter(left);
      const b = sorter(right);
      if (a < b) return sort.dir === "asc" ? -1 : 1;
      if (a > b) return sort.dir === "asc" ? 1 : -1;
      return 0;
    });
  }
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-[11px] uppercase tracking-wide text-muted">
            {columns.map((item) => (
              <th key={item.key} className={`whitespace-nowrap px-3 py-2 font-medium ${item.className ?? ""}`} aria-sort={sort?.key === item.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                {item.sort ? (
                  <button
                    type="button"
                    className="hover:text-ink"
                    onClick={() =>
                      setSort((current) =>
                        current?.key === item.key
                          ? { key: item.key, dir: current.dir === "asc" ? "desc" : "asc" }
                          : { key: item.key, dir: "asc" },
                      )
                    }
                  >
                    {item.header}
                  </button>
                ) : (
                  item.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.id} className="border-b border-line/80 last:border-0 hover:bg-slate-50">
              {columns.map((item) => (
                <td key={item.key} className={`px-3 py-2.5 align-top text-ink ${item.className ?? ""}`}>
                  {item.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
